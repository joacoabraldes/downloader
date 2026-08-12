-- Series de la API oficial del Estado (apis.datos.gob.ar/series), formato LONG.
-- Evaluación completa de la fuente: docs/datos_gob_ar.md
--
-- Modelo append-only: cada corrida inserta un snapshot con su ingested_at; nunca se pisa un
-- dato. Los organismos revisan meses ya publicados, así que `insert_if_changed` deja cada
-- revisión como snapshot nuevo y el anterior queda en la historia.
--
-- Todo entra con estado='definitivo': la API publica un solo dato por período.
--
-- Star-schema, igual que reservas_pasivos y por el mismo motivo: las series NO comparten
-- unidad (conviven índices, dólares y pesos en la misma tabla), así que el nombre legible y la
-- unidad viven en la dimensión y la vista _actual las une por JOIN.
--
-- `serie` no lleva CHECK ni FK a propósito (mismo criterio que reservas_pasivos): sumar una
-- serie es editar SERIES_META en config.py, y un CHECK acá obligaría a tocar dos lugares.

create table if not exists etl_datos_gob (
  id          bigint generated always as identity primary key,
  serie       text   not null,            -- slug propio; ver la dimensión
  date        date   not null,            -- primer día del mes
  valor       double precision,
  estado      text,                       -- 'definitivo' (API) / 'desestacionalizado' (X-13)
  fuente      text,                       -- URL de la serie en la API
  parametros  jsonb,                      -- solo en desest: parámetros de la corrida X-13
  ingested_at timestamptz not null default now()
);
alter table etl_datos_gob add column if not exists parametros jsonb;

create index if not exists etl_datos_gob_serie_date_estado_idx
  on etl_datos_gob (serie, date, estado, ingested_at desc);

create unique index if not exists etl_datos_gob_desest_uq
  on etl_datos_gob (serie, date)
  where estado = 'desestacionalizado';

-- Dimensión: nombre legible, unidad, organismo e id en la API.
--
-- A DIFERENCIA de reservas_pasivos, acá el seed NO se escribe en este archivo: lo upsertea
-- `run.py` en cada corrida desde `config.SERIES_META`, que es donde ya vive el id de la API
-- porque el ETL lo necesita para bajar la serie. Tener las filas también acá sería una segunda
-- copia de la misma verdad, condenada a desincronizarse.
create table if not exists etl_datos_gob_series (
  serie       text primary key,           -- slug propio ('isac', 'ipc_nacional', ...)
  id_api      text not null,              -- id en apis.datos.gob.ar ('33.2_ISAC_NIVELRAL_0_M_18_63')
  nombre      text not null,
  unidad      text,
  organismo   text,
  deflactable boolean not null default false,  -- serie en pesos corrientes: tiene versión real
  orden       int
);
-- Upgrade idempotente para bases ya creadas sin esta columna.
alter table etl_datos_gob_series add column if not exists deflactable boolean not null default false;

-- Serie observada "actual" por (serie, mes): último snapshot, enriquecida con la dimensión.
create or replace view etl_datos_gob_actual as
select distinct on (d.serie, d.date)
    d.serie, c.nombre, c.unidad, c.organismo, c.id_api,
    d.date, d.valor, d.estado, d.fuente, d.ingested_at,
    -- `deflactable` va AL FINAL a propósito: `create or replace view` sólo admite agregar
    -- columnas al final, nunca intercalarlas. Ponerla en el medio rompe el re-apply del schema
    -- sobre una base ya creada (y habría que dropear la vista, que tiene dependientes).
    c.deflactable
from etl_datos_gob d
left join etl_datos_gob_series c on c.serie = d.serie
where d.estado is distinct from 'desestacionalizado'
order by d.serie, d.date, d.ingested_at desc;

-- Serie REAL: las series en pesos corrientes llevadas a precios constantes.
--
--     real(t) = nominal(t) * IPC(base) / IPC(t)
--
-- Es una VISTA, no una tabla, por el mismo criterio con el que no guardamos la variación
-- mensual del ICC: es un derivado exacto de dos valores que ya están guardados, y guardarlo
-- sería una segunda copia de la misma verdad que puede desincronizarse.
--
-- El mes base es FIJO (junio-2026). Con un base móvil ("pesos de hoy") toda la serie real se
-- recalcularía cada mes y los valores dejarían de ser reproducibles. Para cambiarlo, editar la
-- fecha de acá y `MES_BASE_REAL` en config.py, que la documenta.
--
-- COBERTURA: el deflactor es el IPC nacional del propio dataset, que arranca en 2016-12. Los
-- meses anteriores NO tienen valor real: `ripte` (desde 1994) y `smvm` (desde 1965) quedan
-- deflactados sólo de 2016-12 en adelante. Empalmar un IPC más largo es una decisión
-- metodológica que todavía no se tomó (ver docs/datos_gob_ar.md).
create or replace view etl_datos_gob_real as
with ipc as (
    select date, valor from etl_datos_gob_actual where serie = 'ipc_nacional'
), base as (
    select valor from ipc where date = date '2026-06-01'
)
select d.serie, d.nombre, d.unidad, d.date,
       d.valor * (select valor from base) / i.valor as valor,
       d.valor as valor_nominal,
       date '2026-06-01' as mes_base
from etl_datos_gob_actual d
join ipc i on i.date = d.date
where d.deflactable
  and d.valor is not null and i.valor > 0;

-- Serie desestacionalizada (X-13), un valor por (serie, mes).
--
-- Se ajustan sólo las dos series de ventas, y se ajusta su serie REAL, no la nominal: bajo
-- inflación argentina la estacionalidad de una serie en pesos corrientes queda tapada por la
-- deriva de precios, así que desestacionalizar el nominal no dice nada. La vista insumo de
-- X-13 es etl_datos_gob_real (ver la clave `view` en etl/series_desest.toml).
--
-- El resto de las series no se ajusta: los índices de INDEC ya vienen ajustados de origen y
-- correrles X-13 encima sería ajustar dos veces.
create or replace view etl_datos_gob_desest as
select distinct on (serie, date)
    serie, date, valor, fuente, ingested_at, parametros
from etl_datos_gob
where estado = 'desestacionalizado'
order by serie, date, ingested_at desc;

-- Vista de consumo: los tres valores de cada serie/mes en una sola fila.
--   valor_nominal  como lo publica el organismo
--   valor_real     a precios de junio-2026 (NULL si la serie no es deflactable o falta IPC)
--   valor_desest   X-13 sobre la serie real (NULL si esa serie no se desestacionaliza)
create or replace view etl_datos_gob_completo as
select a.serie, a.nombre, a.unidad, a.organismo, a.date,
       a.valor as valor_nominal,
       r.valor as valor_real,
       s.valor as valor_desest,
       r.mes_base
from etl_datos_gob_actual a
left join etl_datos_gob_real   r on r.serie = a.serie and r.date = a.date
left join etl_datos_gob_desest s on s.serie = a.serie and s.date = a.date
order by a.serie, a.date;
