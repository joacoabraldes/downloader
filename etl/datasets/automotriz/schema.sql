-- Industria automotriz (ADEFA), formato LONG: 3 series independientes
-- (produccion, ventas, expo), cada una con su propia desestacionalización X-13.
-- Modelo append-only: cada corrida inserta un snapshot nuevo con su ingested_at;
-- nunca se pisa un dato. Conviven histórico (Excel) y provisorio (PDF) del mismo mes.

create table if not exists automotriz (
  id          bigint generated always as identity primary key,
  serie       text   not null check (serie in ('produccion','ventas','expo')),
  date        date   not null,                 -- primer día del mes
  valor       double precision,                -- unidades
  estado      text,                            -- NULL=histórico (Excel) / provisorio (PDF) / desestacionalizado (X-13)
  fuente      text,                            -- 'excel historico' / URL del PDF ADEFA / 'census x13'
  parametros  jsonb,                           -- solo en desest: parámetros de la corrida X-13 (modo, metodo, ...)
  ingested_at timestamptz not null default now()
);
-- Upgrade idempotente para bases ya creadas sin esta columna.
alter table automotriz add column if not exists parametros jsonb;

-- Búsqueda del último snapshot de un (serie, date, estado).
create index if not exists automotriz_serie_date_estado_idx
  on automotriz (serie, date, estado, ingested_at desc);

-- Una sola fila desestacionalizada por (serie, mes) (UPSERT desde el núcleo X-13).
create unique index if not exists automotriz_desest_uq
  on automotriz (serie, date)
  where estado = 'desestacionalizado';

-- Serie observada "actual" por (serie, mes): último snapshot, priorizando el PDF
-- (provisorio) sobre el histórico del Excel, excluyendo la desestacionalizada. El Excel
-- y el PDF son la MISMA fuente (ADEFA); el PDF es la autoridad para los meses que cubre
-- (corrige valores de borde quedados en el Excel, p.ej. expo 2026-05).
create or replace view automotriz_actual as
select distinct on (serie, date)
    serie, date, valor, estado, fuente, ingested_at
from automotriz
where estado is distinct from 'desestacionalizado'
order by serie, date,
         (case when estado = 'provisorio' then 0 when estado is null then 1 else 2 end),
         ingested_at desc;

-- Serie desestacionalizada (X-13), un valor por (serie, mes).
create or replace view automotriz_desest as
select distinct on (serie, date)
    serie, date, valor, fuente, ingested_at, parametros
from automotriz
where estado = 'desestacionalizado'
order by serie, date, ingested_at desc;
