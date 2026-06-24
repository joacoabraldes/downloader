-- Serie histórica de despacho de cemento (AFCP), formato LONG: 1 fila por (serie, date).
-- Series: despacho_nacional (principal) + exportacion / consumo_despacho_nacional /
-- importaciones_propias (estas 3 solo se llenan en filas 'definitivo').
-- Modelo append-only: cada corrida inserta un snapshot nuevo con su ingested_at, conservando
-- provisorio y definitivo. La "vista actual" toma el último snapshot por (serie, fecha)
-- priorizando definitivo. La serie desestacionalizada (Census X-13) se guarda como
-- estado='desestacionalizado' con UPSERT (1 fila por serie/mes).

create table if not exists cemento_despacho (
  id          bigint generated always as identity primary key,
  serie       text not null check (serie in
                ('despacho_nacional','exportacion','consumo_despacho_nacional','importaciones_propias')),
  date        date    not null,              -- primer día del mes
  valor       double precision,             -- miles de toneladas
  estado      text    check (estado in ('provisorio','definitivo','desestacionalizado')),  -- null = histórico xlsx
  fuente      text,                           -- url de origen / 'census x13' (null para histórico)
  ingested_at timestamptz not null default now()
);

create index if not exists cemento_despacho_serie_date_estado_idx
  on cemento_despacho (serie, date, estado, ingested_at desc);

-- UPSERT de la serie desestacionalizada: a lo sumo 1 fila por (serie, mes) con ese estado.
create unique index if not exists cemento_despacho_desest_uq
  on cemento_despacho (serie, date) where estado = 'desestacionalizado';

-- Valor "actual" por (serie, mes) de la serie OBSERVADA (excluye desestacionalizado):
-- último snapshot con prioridad explícita definitivo > histórico(NULL) > provisorio. Se usa
-- un CASE (no `(estado='definitivo') desc`) porque con NULL ese booleano da NULL y, en DESC,
-- los NULL irían primero (NULLS FIRST) y la fila histórica le ganaría al definitivo.
create or replace view cemento_despacho_actual as
select distinct on (serie, date) serie, date, valor, estado, fuente, ingested_at
from cemento_despacho
where estado is distinct from 'desestacionalizado'
order by serie, date,
         (case when estado = 'definitivo' then 0 when estado is null then 1
               when estado = 'provisorio' then 2 else 3 end),
         ingested_at desc;

-- Serie desestacionalizada (Census X-13), un valor por (serie, mes).
create or replace view cemento_despacho_desest as
select distinct on (serie, date) serie, date, valor, fuente, ingested_at
from cemento_despacho
where estado = 'desestacionalizado'
order by serie, date, ingested_at desc;
