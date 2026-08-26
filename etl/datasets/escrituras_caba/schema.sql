-- Escrituras de compraventa de inmuebles en CABA (Colegio de Escribanos de la Ciudad de
-- Buenos Aires), formato LONG. Serie: `compraventa` = cantidad de actos del mes.
--
-- Modelo append-only: cada corrida inserta un snapshot nuevo con su ingested_at; nunca se
-- pisa un dato. La fuente revisa: el informe de febrero-2016 dice 1919 actos en el texto del
-- post y 1920 en la planilla de un informe posterior. Son revisiones de ±1, que
-- `insert_if_changed` deja como snapshot nuevo dejando el anterior en la historia.
--
-- NO se desestacionaliza (ver etl/series_desest.toml). Es una decisión, no un olvido: la
-- serie tiene 6 huecos que la fuente nunca publicó y X-13 exige meses contiguos, y además no
-- hay ninguna referencia oficial desestacionalizada contra la cual calibrar. La vista
-- `_desest` existe igual y devuelve 0 filas, para que el dataset tenga la misma forma que los
-- demás: sumarlo después es sólo agregar el bloque en el toml.

create table if not exists etl_escrituras_caba (
  id          bigint generated always as identity primary key,
  serie       text   not null check (serie in ('compraventa')),
  date        date   not null,                 -- primer día del mes al que se refiere el dato
  valor       double precision,                -- cantidad de actos
  estado      text,                            -- definitivo (texto del informe) / relleno (planilla de otro informe) / desestacionalizado
  fuente      text,                            -- URL del post o de la planilla .xls
  parametros  jsonb,                           -- solo en desest: parámetros de la corrida X-13
  ingested_at timestamptz not null default now()
);

-- Búsqueda del último snapshot de un (serie, date, estado).
create index if not exists etl_escrituras_caba_serie_date_estado_idx
  on etl_escrituras_caba (serie, date, estado, ingested_at desc);

-- Una sola fila desestacionalizada por (serie, mes), si algún día se ajusta.
create unique index if not exists etl_escrituras_caba_desest_uq
  on etl_escrituras_caba (serie, date)
  where estado = 'desestacionalizado';

-- Serie observada "actual" por (serie, mes): último snapshot, excluyendo la desest.
--
-- `definitivo` (el número del texto del informe de ESE mes) le gana a `relleno` (el mismo mes
-- leído en la planilla rodante de un informe POSTERIOR). El relleno existe sólo porque la
-- fuente no publicó informe para 6 meses; si alguna vez aparece el informe propio, su valor
-- pasa a mandar sin que haya que borrar nada.
create or replace view etl_escrituras_caba_actual as
select distinct on (serie, date)
    serie, date, valor, estado, fuente, ingested_at
from etl_escrituras_caba
where estado is distinct from 'desestacionalizado'
order by serie, date,
         (case when estado = 'definitivo' then 0 when estado = 'relleno' then 1 else 2 end),
         ingested_at desc;

-- Serie desestacionalizada (X-13), un valor por (serie, mes). Hoy queda vacía a propósito
-- (ver el comentario del encabezado).
create or replace view etl_escrituras_caba_desest as
select distinct on (serie, date)
    serie, date, valor, fuente, ingested_at, parametros
from etl_escrituras_caba
where estado = 'desestacionalizado'
order by serie, date, ingested_at desc;
