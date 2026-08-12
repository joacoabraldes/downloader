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

-- LA vista para la app: los 14 datasets SIEMPRE, hayan corrido o no.
--
--   select * from etl_control_salud where estado <> 'ok';
--
-- `horas_max` es el hueco legítimo más largo que puede haber entre dos corridas según la
-- ventana del cron (p.ej. cemento corre los días 1-10: del día 10 al 1 del mes siguiente pasan
-- ~21 días sin correr, y está bien). Pasarse de ahí significa que el cron dejó de disparar.
create or replace view etl_control_salud as
with esperado(dataset, horas_max) as (values
    ('cemento',         530),   -- cron 1-10   -> hueco max ~22 dias
    ('automotriz',       80),   -- cron diario (ADEFA no tiene fecha previsible) -> hueco max
                                --   viernes 12:10 a lunes 12:10 ~72 h: la VM esta apagada el finde
    ('patentamientos',  530),   -- cron 1-10
    ('acero',           130),   -- cron 15-31,1-10 -> hueco max 5 dias (del 10 al 15)
    ('granos',          450),   -- cron 18-31  -> hueco max ~18 dias
    ('aves',            500),   -- cron 20-31  -> hueco max ~20 dias
    ('bovinos',          80),   -- cron diario: MAGyP movio la fecha (jun salio el 20-jul y
                                --   jul el 7-ago), la ventana 20-31 lo perdia. ~72 h por el finde
    ('leche',           260),   -- cron 20-31,1-10 -> hueco max ~10 dias
    ('demanda_energia',  80),   -- cron diario -> hueco max viernes 12:00 a lunes 12:00 ~72 h:
                                --   la VM esta apagada el finde (con 26 h daba SIN_CORRER los lunes)
    ('icc',             470),   -- cron 17-31 -> hueco max ~17 dias (del 31 al 17) + finde
    ('icg',             580),   -- cron 22-31 -> hueco max ~22 dias (del 31 al 22) + finde
    ('datos_gob',        80),   -- cron diario: son 9 series de organismos distintos, cada una
                                --   con su propio calendario. Hueco max viernes a lunes ~72 h.
    ('reservas_pasivos', 80),   -- cron L-V -> fin de semana ~71 h
    ('compras_granos',   80)    -- cron L-V -> fin de semana ~71 h (ver nota de la ventana abajo)
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
       end as estado
from esperado e
left join etl_control_ultima u using (dataset)
order by (case when u.dataset is null then 0
               when u.estado = 'falla' then 1
               when u.horas_desde > e.horas_max then 2
               else 3 end), e.dataset;
