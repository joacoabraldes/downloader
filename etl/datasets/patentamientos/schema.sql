-- Patentamientos 0km del mercado 4W (SIOMAA), formato LONG: una serie por categoría
-- del mercado (autos, comerciales, total, ...), cada una con su propia desest X-13.
-- Modelo append-only: cada corrida inserta un snapshot nuevo con su ingested_at; nunca
-- se pisa un dato. Todo sale del PDF mensual de SIOMAA (Tabla 1, "Resumen del mercado").
--
-- Fuente distinta de `automotriz` (ADEFA = producción/ventas/expo de fábrica): acá son
-- registraciones (patentamientos). Sólo se guardan las UNIDADES del mes por categoría;
-- las variaciones/acumulados del PDF son recalculables y no se persisten.

create table if not exists etl_patentamientos (
  id          bigint generated always as identity primary key,
  serie       text   not null check (serie in (
                 'total_mercado','autos','comercial_liviano','comercial_pesado',
                 'otros_pesados','autos_cl','autos_cl_cp')),
  date        date   not null,                 -- primer día del mes del informe
  valor       double precision,                -- unidades patentadas en el mes
  estado      text,                            -- NULL=histórico (backfill PDFs) / definitivo (run mensual: SIOMAA publica el dato final) / desestacionalizado (X-13)
  fuente      text,                            -- nombre del informe SIOMAA / 'census x13'
  parametros  jsonb,                           -- solo en desest: parámetros de la corrida X-13
  ingested_at timestamptz not null default now()
);
-- Upgrade idempotente para bases ya creadas sin esta columna.
alter table etl_patentamientos add column if not exists parametros jsonb;

-- Búsqueda del último snapshot de un (serie, date, estado).
create index if not exists etl_patentamientos_serie_date_estado_idx
  on etl_patentamientos (serie, date, estado, ingested_at desc);

-- Una sola fila desestacionalizada por (serie, mes) (UPSERT desde el núcleo X-13).
create unique index if not exists etl_patentamientos_desest_uq
  on etl_patentamientos (serie, date)
  where estado = 'desestacionalizado';

-- Serie observada "actual" por (serie, mes): último snapshot, excluyendo la desest. El
-- mensual ('definitivo', el dato final que publica SIOMAA) tiene prioridad sobre el
-- histórico del backfill (NULL) para los meses que ambos cubran.
create or replace view etl_patentamientos_actual as
select distinct on (serie, date)
    serie, date, valor, estado, fuente, ingested_at
from etl_patentamientos
where estado is distinct from 'desestacionalizado'
order by serie, date,
         (case when estado = 'definitivo' then 0 when estado = 'provisorio' then 1
               when estado is null then 2 else 3 end),
         ingested_at desc;

-- Serie desestacionalizada (X-13), un valor por (serie, mes).
create or replace view etl_patentamientos_desest as
select distinct on (serie, date)
    serie, date, valor, fuente, ingested_at, parametros
from etl_patentamientos
where estado = 'desestacionalizado'
order by serie, date, ingested_at desc;
