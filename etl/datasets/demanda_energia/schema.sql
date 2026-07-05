-- Demanda energética del MEM (CAMMESA), formato LONG. Serie principal: no_residencial (GWh).
-- Modelo append-only: cada corrida inserta un snapshot nuevo con su ingested_at; nunca se
-- pisa un dato. Conviven el histórico profundo de no_residencial (energia.xlsx, 2005→2022,
-- estado NULL) y el definitivo del Excel "Demanda Mensual" de CAMMESA (2023→, todas las series).

create table if not exists etl_demanda_energia (
  id          bigint generated always as identity primary key,
  serie       text   not null check (serie in (
                'estacionalizada', 'residencial', 'no_res_estacionalizada',
                'no_estacionalizada', 'gudi', 'gume', 'guma', 'mate_distribuidor',
                'local', 'no_residencial')),
  date        date   not null,                 -- primer día del mes
  valor       double precision,                -- GWh (fuente CAMMESA en MWh, ÷1000)
  estado      text,                            -- NULL=histórico (energia.xlsx) / definitivo (CAMMESA) / desestacionalizado (X-13)
  fuente      text,                            -- 'excel historico' / URL del xlsx de CAMMESA / 'census x13'
  parametros  jsonb,                           -- solo en desest: parámetros de la corrida X-13
  ingested_at timestamptz not null default now()
);
-- Upgrade idempotente para bases ya creadas sin esta columna.
alter table etl_demanda_energia add column if not exists parametros jsonb;

-- Búsqueda del último snapshot de un (serie, date, estado).
create index if not exists etl_demanda_energia_serie_date_estado_idx
  on etl_demanda_energia (serie, date, estado, ingested_at desc);

-- Una sola fila desestacionalizada por (serie, mes) (UPSERT desde el núcleo X-13).
create unique index if not exists etl_demanda_energia_desest_uq
  on etl_demanda_energia (serie, date)
  where estado = 'desestacionalizado';

-- Serie observada "actual" por (serie, mes): último snapshot, excluyendo la desest. El
-- definitivo de CAMMESA tiene prioridad sobre el histórico (NULL) en los meses que ambos cubran.
create or replace view etl_demanda_energia_actual as
select distinct on (serie, date)
    serie, date, valor, estado, fuente, ingested_at
from etl_demanda_energia
where estado is distinct from 'desestacionalizado'
order by serie, date,
         (case when estado = 'definitivo' then 0 when estado = 'provisorio' then 1
               when estado is null then 2 else 3 end),
         ingested_at desc;

-- Serie desestacionalizada (X-13), un valor por (serie, mes).
create or replace view etl_demanda_energia_desest as
select distinct on (serie, date)
    serie, date, valor, fuente, ingested_at, parametros
from etl_demanda_energia
where estado = 'desestacionalizado'
order by serie, date, ingested_at desc;
