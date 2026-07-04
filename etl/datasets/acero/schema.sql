-- Producción siderúrgica argentina (Cámara Argentina del Acero), formato LONG. Hoy una sola
-- serie: acero_crudo (miles de toneladas). Modelo append-only: cada corrida inserta un
-- snapshot nuevo con su ingested_at; nunca se pisa un dato. Conviven el histórico (Excel,
-- estado NULL) y el provisorio del PDF mensual (que la CAA revisa en su ventana de 13 meses).

create table if not exists acero (
  id          bigint generated always as identity primary key,
  serie       text   not null check (serie in ('acero_crudo')),
  date        date   not null,                 -- primer día del mes
  valor       double precision,                -- miles de toneladas
  estado      text,                            -- NULL=histórico (Excel) / provisorio (PDF CAA) / desestacionalizado (X-13)
  fuente      text,                            -- 'excel historico' / URL del PDF de Cifras / 'census x13'
  parametros  jsonb,                           -- solo en desest: parámetros de la corrida X-13
  ingested_at timestamptz not null default now()
);
-- Upgrade idempotente para bases ya creadas sin esta columna.
alter table acero add column if not exists parametros jsonb;

-- Búsqueda del último snapshot de un (serie, date, estado).
create index if not exists acero_serie_date_estado_idx
  on acero (serie, date, estado, ingested_at desc);

-- Una sola fila desestacionalizada por (serie, mes) (UPSERT desde el núcleo X-13).
create unique index if not exists acero_desest_uq
  on acero (serie, date)
  where estado = 'desestacionalizado';

-- Serie observada "actual" por (serie, mes): último snapshot, excluyendo la desest. El PDF
-- (provisorio, que corrige la ventana de 13 meses) tiene prioridad sobre el histórico (NULL).
create or replace view acero_actual as
select distinct on (serie, date)
    serie, date, valor, estado, fuente, ingested_at
from acero
where estado is distinct from 'desestacionalizado'
order by serie, date,
         (case when estado = 'provisorio' then 0 when estado is null then 1 else 2 end),
         ingested_at desc;

-- Serie desestacionalizada (X-13), un valor por (serie, mes).
create or replace view acero_desest as
select distinct on (serie, date)
    serie, date, valor, fuente, ingested_at, parametros
from acero
where estado = 'desestacionalizado'
order by serie, date, ingested_at desc;
