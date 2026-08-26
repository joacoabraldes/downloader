-- Control de ejecución de los ETLs: una fila POR CORRIDA, ande o no.
--
-- Por qué existe: las tablas de datos son append-only con `insert_if_changed`, así que una
-- corrida que no encuentra cambios NO escribe nada. `max(ingested_at)` es "último día que un
-- valor cambió", no "último día que el ETL corrió": un ETL muerto hace tres meses se ve igual
-- que uno que corre todos los días sin novedad. Mirando los datos se ve si el DATO está viejo;
-- esta tabla es la que dice si el PROCESO está vivo.
--
-- La escribe `etl/core/control.py` desde `python -m etl`, en un `finally`: se registra igual si
-- la corrida falla o si revienta con una excepción no capturada.

create table if not exists etl_control_ejecucion (
  id            bigint generated always as identity primary key,
  dataset       text        not null,          -- 'aves', 'granos', ... o 'redesest'
  comando       text        not null,          -- 'run' | 'load-history' | 'redesest'
  inicio        timestamptz not null,
  fin           timestamptz not null default now(),
  duracion_seg  numeric,
  estado        text        not null check (estado in ('ok', 'falla')),
  fallas        text[],                        -- descripciones de las fallas; NULL si estado='ok'
  -- Contadores del reporte (mismos que la línea `resumen [...]` del log).
  leidos        integer,
  nuevos        integer,
  actualizados  integer,
  sin_cambios   integer,
  saltados      integer,
  no_publicado  integer,
  -- Etapa X-13, cuando corrió.
  desest_series   integer,
  desest_upserts  integer,
  desest_saltadas integer,
  -- Último mes/día observado en la tabla del dataset DESPUÉS de la corrida: junta en una sola
  -- fila "el proceso corrió" y "hasta dónde llega el dato".
  ultimo_dato   date,
  host          text
);

create index if not exists etl_control_ejecucion_dataset_inicio_idx
  on etl_control_ejecucion (dataset, inicio desc);

-- Última corrida de cada dataset (sólo los que alguna vez corrieron).
create or replace view etl_control_ultima as
select distinct on (dataset)
       dataset,
       comando,
       inicio,
       fin,
       duracion_seg,
       estado,
       fallas,
       leidos,
       nuevos,
       actualizados,
       ultimo_dato,
       round(extract(epoch from (now() - fin)) / 3600.0, 1) as horas_desde
from etl_control_ejecucion
where comando <> 'load-history'   -- carga manual one-off, no es señal de que el cron viva
order by dataset, inicio desc;

-- LA vista para la app: los 17 datasets SIEMPRE, hayan corrido o no.
--
--   select * from etl_control_salud where estado <> 'ok';        -- el PROCESO esta roto
--   select * from etl_control_salud where estado_dato <> 'ok';   -- la FUENTE dejo de publicar
--
-- Son dos preguntas distintas y por eso son dos columnas distintas, no un unico veredicto:
--
--   `estado`      mide el PROCESO. Se cae si el cron dejo de disparar o si la corrida fallo.
--                 Accionable por nosotros: hay algo que arreglar en esta maquina o en el codigo.
--   `estado_dato` mide el DATO. Se cae si la fuente no publico nada nuevo en el plazo esperado.
--                 NO es accionable por nosotros: el ETL puede estar corriendo perfecto todos los
--                 dias y devolver `sin_cambios` porque el organismo todavia no publico.
--
-- Meterlos en una sola columna haria que `where estado <> 'ok'` mezcle "se rompio mi codigo,
-- arreglalo ahora" con "el INDEC viene tarde, espera" -- dos urgencias que no se parecen en nada.
-- Ademas romperia a los consumidores actuales de `estado`, que hoy significa exactamente
-- "el proceso esta vivo". Separadas, cada alerta va a quien la puede resolver.
--
-- `horas_max` es el hueco legítimo más largo que puede haber entre dos corridas según la
-- ventana del cron (p.ej. cemento corre los días 1-10: del día 10 al 1 del mes siguiente pasan
-- ~21 días sin correr, y está bien). Pasarse de ahí significa que el cron dejó de disparar.
--
-- `dias_max_dato` es la edad máxima legítima de `ultimo_dato`. Sale de una cuenta, no del ojo:
--
--     dias_max_dato = EDAD DEL LABEL AL PUBLICARSE + un periodo de la serie + margen
--
-- OJO CON EL PRIMER TERMINO: no es el rezago de publicacion de la fuente. Es la edad que tiene
-- `ultimo_dato` el dia en que la fuente publica, y como `date` es el PRIMER dia del periodo, ya
-- trae adentro el periodo entero transcurriendo. Ejemplo con automotriz:
--
--     ADEFA publica junio el 04-jul  ->  rezago REAL de la fuente = 4 dias (junio cierra el 30-jun)
--     pero `ultimo_dato` vale 2026-06-01  ->  edad del label ese dia = 33 dias (29 + 4)
--
-- Los 33 son el numero que va en la cuenta, porque el umbral compara contra `ultimo_dato`, no
-- contra el cierre del periodo. Confundirlos hace leer "acero: 60 d" como "el INDEC tarda dos
-- meses", cuando en realidad tarda ~30 dias desde que cierra el mes.
--
-- El "+ un periodo" es el otro termino que se olvida: `ultimo_dato` no se queda quieto esperando,
-- ENVEJECE hasta que aterriza el dato siguiente. El junio de aves recien es reemplazado por el
-- julio a fines de agosto, y mientras tanto su edad llega a ~91 dias sin que pase nada malo. Un
-- umbral igual a la edad del label daria falsa alarma todos los meses.
--
-- La edad del label al publicarse se midio contra `min(ingested_at)` por fecha en cada tabla,
-- descartando los lotes del backfill inicial (se reconocen porque cientos de fechas comparten el
-- mismo `ingested_at`). Los umbrales son deliberadamente generosos: una alerta que grita al pedo
-- se termina ignorando, y entonces no sirve para nada. Reajustar cuando haya varios meses de
-- observacion incremental real.
create or replace view etl_control_salud as
with esperado(dataset, horas_max, dias_max_dato) as (values
    ('cemento',         530,  80),   -- cron 1-10   -> hueco max ~22 dias
                                     --   dato: edad del label al publicar 32-37 d (jun visto 03-jul, jul visto 07-ago)
    ('automotriz',      530, 105),   -- cron 1-10 desde el 14/08/2026 -> hueco max ~22 dias (del 10
                                     --   al 1 del mes siguiente), igual que cemento y patentamientos
                                     --   dato: edad del label al publicar 33 d (jun visto 04-jul) CON
                                     --   cron diario. Con la ventana 1-10 el umbral tiene que ser
                                     --   MAS grande, no mas chico: si ADEFA publica pasado el dia 10
                                     --   -- que es lo que paso con julio-2026 -- el ETL recien lo ve
                                     --   el 1 del mes siguiente, y `ultimo_dato` puede llegar a ~92 d
                                     --   antes de que lo reemplace el mes que sigue. 105 cubre eso.
                                     --   El respaldo por gacetillas NO ayuda a acortarlo: adelanta el
                                     --   dato solo si el ETL corre, y con esta ventana no corre entre
                                     --   el 11 y el 31.
    ('patentamientos',  530,  80),   -- cron 1-10
                                     --   dato: edad del label al publicar 31 d (jul visto 01-ago)
    ('acero',           130, 105),   -- cron 15-31,1-10 -> hueco max 5 dias (del 10 al 15)
                                     --   dato: edad del label al publicar 60 d (jun visto 31-jul) -> 60+31 = 91 + margen
    ('granos',          450,  95),   -- cron 18-31  -> hueco max ~18 dias
                                     --   dato: edad del label al publicar 50 d (jun visto 21-jul)
    ('aves',            500, 105),   -- cron 20-31  -> hueco max ~20 dias
                                     --   dato: edad del label al publicar 60 d (jun visto 31-jul)
    ('bovinos',          80,  95),   -- cron diario: MAGyP movio la fecha (jun salio el 20-jul y
                                     --   jul el 7-ago), la ventana 20-31 lo perdia. ~72 h por el finde
                                     --   dato: edad del label al publicar 42-49 d, y la fecha se mueve -> margen ancho
    ('leche',           260,  95),   -- cron 20-31,1-10 -> hueco max ~10 dias
                                     --   dato: edad del label al publicar 49 d (jun visto 20-jul)
    ('hidrocarburos',   260, 105),   -- cron 20-31,1-10 -> hueco max ~10 dias (del 10 al 20), igual que leche
                                     --   dato: la Secretaria de Energia publica el capitulo IV a
                                     --   fines del mes siguiente (julio-2026 ya estaba el 26-ago) ->
                                     --   edad del label al publicar ~50-56 d. 56 + 31 de periodo +
                                     --   margen = 105.
                                     --   OJO: umbral ESTIMADO, no medido. Al 2026-08 no hay corridas
                                     --   incrementales todavia; la unica observacion es que el dato de
                                     --   julio estaba disponible el 26-ago. Reajustar cuando haya
                                     --   varios meses de incremental real, como se hizo con comex.
    ('demanda_energia',  80, 105),   -- cron diario -> hueco max viernes 12:00 a lunes 12:00 ~72 h:
                                     --   la VM esta apagada el finde (con 26 h daba SIN_CORRER los lunes)
                                     --   dato: edad del label al publicar 60 d (jun visto 31-jul)
    ('escrituras_caba', 580, 105),   -- cron 22-31 -> hueco max ~22 dias (del 31 al 22), igual que icg
                                     --   dato: MEDIDO sobre los 120 informes publicados. Los ultimos 24
                                     --   salen entre el dia 22 y el 26 del mes siguiente, con la edad del
                                     --   label en 51-56 d (mediana 52). 56 + 31 de periodo = 87 + margen.
                                     --   El margen es ancho a proposito: en 2016-2017 la fuente se
                                     --   atrasaba mucho mas (maximo historico 96 d, dic-2016 salio el
                                     --   01-mar-2017). Si vuelve a aflojar el ritmo, 95 daria falsa alarma.
    ('icc',             470,  70),   -- cron 17-31 -> hueco max ~17 dias (del 31 al 17) + finde
                                     --   dato: UTDT publica DENTRO del mes de referencia (ICC un
                                     --   jueves entre el 17 y el 24), no al mes siguiente: el label
                                     --   llega a ~24 d, no a ~50 -> 24+31 = 55 + margen. Sin observacion
                                     --   incremental todavia (el backfill del 12-ago trajo hasta
                                     --   jul); si en oct-2026 no disparo falsos, dejarlo asi.
    ('icg',             580,  70),   -- cron 22-31 -> hueco max ~22 dias (del 31 al 22) + finde
                                     --   dato: idem icc, ICG sale un lunes entre el 22 y el 28.
    ('datos_gob',        80,  75),   -- cron diario: son 14 series de organismos distintos, cada una
                                     --   con su propio calendario. Hueco max viernes a lunes ~72 h.
                                     --   dato: `ultimo_dato` es el MAX sobre todas las series, asi
                                     --   que avanza en cuanto publica la mas rapida. Ojo: este umbral
                                     --   NO detecta una serie individual congelada, solo el corte total.
    ('comex',           450,  95),   -- cron 18-31 -> hueco max ~18 dias (del 31 al 18), igual que granos
                                     --   dato: INDEC publica el ICA a mediados del mes siguiente (junio-2026
                                     --   salio el 20-jul: la planilla quedo guardada ese dia) -> edad del
                                     --   label al publicar ~50 d. 50 + 31 de periodo + margen = 95.
                                     --   Medido sobre el `Last Saved` de las dos planillas, no sobre
                                     --   ingested_at: al 2026-08 no hay corridas incrementales todavia.
                                     --   Reajustar cuando haya varios meses de observacion real.
    ('reservas_pasivos', 80,  11),   -- cron L-V 10:00 y 16:30 -> hueco max vie 16:30 a lun 10:00 ~65 h
                                     --   dato: 8 -> 11 el 16/08/2026, junto con el cambio de horario del cron.
                                     --   Medido sobre las 27 fechas de incremental real (descartando los lotes
                                     --   del backfill, que comparten ingested_at): lag de 2 a 6 dias con el
                                     --   horario viejo (19:15, que agarraba la publicacion de ~18:22 el mismo
                                     --   dia). Con 10:00 + 16:30 ninguna pasada ve la publicacion del dia: el
                                     --   dato entra a la mañana siguiente, +1 dia, y +3 si publica un viernes.
                                     --   6 + 3 = 9, + 1 dia habil de periodo + margen = 11. Con 8 disparaba
                                     --   DATO_VIEJO falso. Si se vuelve al horario 19:15/20:30, volver a 8.
    ('compras_granos',   80,  25)    -- cron L-V -> fin de semana ~71 h (ver nota de la ventana abajo)
                                     --   dato: edad del label al publicar 7-11 d medido sobre 3 semanas (el corte 05-ago
                                     --   entro el 12-ago) -> 11 + 7 de periodo + margen
)
select e.dataset,
       u.estado          as estado_ultima_corrida,
       u.fin             as ultima_corrida,
       u.horas_desde,
       e.horas_max,
       u.ultimo_dato,
       u.fallas,
       case when u.dataset is null              then 'NUNCA_CORRIO'
            when u.estado = 'falla'             then 'FALLA'
            when u.horas_desde > e.horas_max    then 'SIN_CORRER'
            else 'ok'
       end as estado,
       -- Columnas nuevas al FINAL a proposito: `create or replace view` en Postgres solo permite
       -- AGREGAR columnas al final. Insertarlas en el medio obligaria a un `drop view` y a recrear
       -- todo lo que dependa de esta vista.
       (current_date - u.ultimo_dato)                     as dias_dato,
       e.dias_max_dato,
       case when u.ultimo_dato is null                    then 'SIN_DATO'
            when (current_date - u.ultimo_dato) > e.dias_max_dato then 'DATO_VIEJO'
            else 'ok'
       end as estado_dato
from esperado e
left join etl_control_ultima u using (dataset)
order by (case when u.dataset is null then 0
               when u.estado = 'falla' then 1
               when u.horas_desde > e.horas_max then 2
               -- El dato viejo ordena DESPUES de los problemas de proceso: si el cron esta caido,
               -- el dato viejo es consecuencia, no causa. Primero se arregla lo que es nuestro.
               when u.ultimo_dato is null then 3
               when (current_date - u.ultimo_dato) > e.dias_max_dato then 4
               else 5 end), e.dataset;
