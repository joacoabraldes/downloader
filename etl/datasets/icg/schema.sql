-- Índice de Confianza en el Gobierno (UTDT), formato LONG. Una sola serie: icg (escala 0-5).
--
-- Modelo append-only: cada corrida inserta un snapshot nuevo con su ingested_at; nunca se
-- pisa un dato. La planilla de UTDT se republica entera todos los meses, así que
-- `insert_if_changed` deja cada revisión como snapshot nuevo y el anterior queda en la historia.
--
-- Todo entra con estado='definitivo': UTDT publica un solo dato por mes, no hay provisorio.
--
-- La serie es continua desde 2001-11, sin huecos.

create table if not exists etl_icg (
  id          bigint generated always as identity primary key,
  serie       text   not null check (serie in ('icg')),
  date        date   not null,                 -- primer día del mes
  valor       double precision,                -- índice (escala 0-5)
  estado      text,                            -- definitivo (planilla UTDT) / desestacionalizado (X-13)
  fuente      text,                            -- URL del .xls de esa corrida / 'census x13'
  parametros  jsonb,                           -- solo en desest: parámetros de la corrida X-13
  ingested_at timestamptz not null default now()
);
-- Upgrade idempotente para bases ya creadas sin esta columna.
alter table etl_icg add column if not exists parametros jsonb;

-- Búsqueda del último snapshot de un (serie, date, estado).
create index if not exists etl_icg_serie_date_estado_idx
  on etl_icg (serie, date, estado, ingested_at desc);

-- Una sola fila desestacionalizada por (serie, mes) (UPSERT desde el núcleo X-13).
create unique index if not exists etl_icg_desest_uq
  on etl_icg (serie, date)
  where estado = 'desestacionalizado';

-- Serie observada "actual" por (serie, mes): último snapshot, excluyendo la desest.
create or replace view etl_icg_actual as
select distinct on (serie, date)
    serie, date, valor, estado, fuente, ingested_at
from etl_icg
where estado is distinct from 'desestacionalizado'
order by serie, date, ingested_at desc;

-- Serie desestacionalizada (X-13), un valor por (serie, mes). Hoy queda vacía: el ICG no se
-- desestacionaliza (ver etl/series_desest.toml). La vista existe para que el dataset tenga la
-- misma forma que los demás y sumarlo sea sólo agregar el bloque en el toml.
create or replace view etl_icg_desest as
select distinct on (serie, date)
    serie, date, valor, fuente, ingested_at, parametros
from etl_icg
where estado = 'desestacionalizado'
order by serie, date, ingested_at desc;
