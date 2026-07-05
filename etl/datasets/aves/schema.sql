-- Sector avícola (MAGyP), formato LONG. Hoy una sola serie: faena (miles de cabezas).
-- Modelo append-only: cada corrida inserta un snapshot nuevo con su ingested_at; nunca se
-- pisa un dato. Conviven el histórico profundo (Excel de referencia, 1981→, estado NULL) y
-- el definitivo del Excel de indicadores mensual de MAGyP (que revisa los últimos meses).

create table if not exists etl_aves (
  id          bigint generated always as identity primary key,
  serie       text   not null check (serie in ('faena')),
  date        date   not null,                 -- primer día del mes
  valor       double precision,                -- miles de cabezas
  estado      text,                            -- NULL=histórico (Excel) / definitivo (indicadores MAGyP) / desestacionalizado (X-13)
  fuente      text,                            -- 'excel historico' / URL del xlsx de indicadores / 'census x13'
  parametros  jsonb,                           -- solo en desest: parámetros de la corrida X-13
  ingested_at timestamptz not null default now()
);
-- Upgrade idempotente para bases ya creadas sin esta columna.
alter table etl_aves add column if not exists parametros jsonb;

-- Búsqueda del último snapshot de un (serie, date, estado).
create index if not exists etl_aves_serie_date_estado_idx
  on etl_aves (serie, date, estado, ingested_at desc);

-- Una sola fila desestacionalizada por (serie, mes) (UPSERT desde el núcleo X-13).
create unique index if not exists etl_aves_desest_uq
  on etl_aves (serie, date)
  where estado = 'desestacionalizado';

-- Serie observada "actual" por (serie, mes): último snapshot, excluyendo la desest. El
-- indicador mensual de MAGyP (definitivo) tiene prioridad sobre el histórico (NULL) para los
-- meses que ambos cubran.
create or replace view etl_aves_actual as
select distinct on (serie, date)
    serie, date, valor, estado, fuente, ingested_at
from etl_aves
where estado is distinct from 'desestacionalizado'
order by serie, date,
         (case when estado = 'definitivo' then 0 when estado = 'provisorio' then 1
               when estado is null then 2 else 3 end),
         ingested_at desc;

-- Serie desestacionalizada (X-13), un valor por (serie, mes).
create or replace view etl_aves_desest as
select distinct on (serie, date)
    serie, date, valor, fuente, ingested_at, parametros
from etl_aves
where estado = 'desestacionalizado'
order by serie, date, ingested_at desc;
