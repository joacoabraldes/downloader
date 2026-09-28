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

-- `etl_control_salud`, LA vista para la app, NO vive acá sino en `etl/schema_control_salud.sql`:
-- lee `etl_datos_gob_salud` (frescura por serie de datos_gob), que la crea el schema de ese
-- dataset, y `init-db` aplica este archivo ANTES que los datasets. Ver `etl/initdb.py`.
