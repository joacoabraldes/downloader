-- Esquema de molienda de granos oleaginosos (MAGyP), formato LONG: 1 fila por (serie, date).
-- Series: total (= suma de los 7 granos) + soja/girasol/lino/mani/algodon/cartamo/canola.
-- Modelo append-only: cada corrida inserta un snapshot nuevo con su ingested_at; nunca se
-- pisa un dato. Conviven histórico (Excel, estado NULL) y provisorio (HTML) del mismo mes.

create table if not exists molienda_granos (
    id          bigint generated always as identity primary key,
    serie       text   not null check (serie in
                  ('total','soja','girasol','lino','mani','algodon','cartamo','canola')),
    date        date   not null,                 -- primer día del mes
    valor       double precision,                -- toneladas
    estado      text,                            -- NULL=histórico (Excel) / provisorio (HTML) / desestacionalizado (X-13)
    fuente      text,                            -- URL del HTML / 'excel historico' / 'census x13'
    ingested_at timestamptz not null default now()
);

-- Búsqueda del último snapshot de un (serie, date, estado).
create index if not exists molienda_granos_serie_date_estado_idx
    on molienda_granos (serie, date, estado, ingested_at desc);

-- Una sola fila desestacionalizada por (serie, mes) (UPSERT desde el núcleo X-13).
create unique index if not exists molienda_granos_desest_uq
    on molienda_granos (serie, date)
    where estado = 'desestacionalizado';

-- Serie observada "actual" por (serie, mes): último snapshot, priorizando histórico (NULL)
-- sobre provisorio, excluyendo la serie desestacionalizada.
create or replace view molienda_granos_actual as
select distinct on (serie, date)
    serie, date, valor, estado, fuente, ingested_at
from molienda_granos
where estado is distinct from 'desestacionalizado'
order by serie, date,
         (case when estado is null then 0 when estado = 'provisorio' then 1 else 2 end),
         ingested_at desc;

-- Serie desestacionalizada (X-13), un valor por (serie, mes).
create or replace view molienda_granos_desest as
select distinct on (serie, date)
    serie, date, valor, fuente, ingested_at
from molienda_granos
where estado = 'desestacionalizado'
order by serie, date, ingested_at desc;
