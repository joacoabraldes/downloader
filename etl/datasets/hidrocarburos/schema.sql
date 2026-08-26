-- Producción de hidrocarburos (Secretaría de Energía), formato LONG.
--
-- Series principales: `petroleo` (miles de m3) y `gas` (millones de m3), cada una con su
-- propia desestacionalización X-13. Además se guarda el desagregado por tipo de recurso
-- (`<serie>_convencional` / `_shale` / `_tight`), que la fuente trae en el mismo CSV.
--
-- OJO CON LAS UNIDADES: no son las mismas entre series. `petroleo` está en MILES de m3 y
-- `gas` en MILLONES de m3. Las dos salen de dividir por 1000 lo que publica la fuente (que
-- viene en miles de m3 en ambos datasets, aunque la columna del de gas se llame
-- `cantidad_mm3`). Ver el docstring de source.py.
--
-- Modelo append-only: cada corrida inserta un snapshot nuevo con su ingested_at; nunca se
-- pisa un dato. Conviven el histórico (Excel, 1996→2008 para los totales) y lo que baja de
-- Superset (2009→, todas las series).

create table if not exists etl_hidrocarburos (
  id          bigint generated always as identity primary key,
  serie       text   not null check (serie in (
                'petroleo', 'petroleo_convencional', 'petroleo_shale', 'petroleo_tight',
                'gas',      'gas_convencional',      'gas_shale',      'gas_tight')),
  date        date   not null,                 -- primer día del mes
  valor       double precision,                -- petroleo: miles de m3 | gas: millones de m3
  estado      text,                            -- NULL=histórico (Excel) / provisorio (Superset) / desestacionalizado (X-13)
  fuente      text,                            -- 'excel historico' / URL del chart de Superset / 'census x13'
  parametros  jsonb,                           -- solo en desest: parámetros de la corrida X-13
  ingested_at timestamptz not null default now()
);

-- Búsqueda del último snapshot de un (serie, date, estado).
create index if not exists etl_hidrocarburos_serie_date_estado_idx
  on etl_hidrocarburos (serie, date, estado, ingested_at desc);

-- Una sola fila desestacionalizada por (serie, mes) (UPSERT desde el núcleo X-13).
create unique index if not exists etl_hidrocarburos_desest_uq
  on etl_hidrocarburos (serie, date)
  where estado = 'desestacionalizado';

-- Serie observada "actual" por (serie, mes): último snapshot, priorizando Superset
-- (provisorio) sobre el histórico del Excel, excluyendo la desestacionalizada.
--
-- Por qué Superset gana en el solapamiento (2009→): es la fuente PRIMARIA —las declaraciones
-- juradas por pozo del capítulo IV— mientras que el histórico del Excel viene de una
-- compilación secundaria del Ministerio de Economía (dataset 41 de la Subsecretaría de
-- Programación Macroeconómica), que corre ~1% por debajo desde 2019 porque no absorbe las
-- correcciones retroactivas de las DDJJ. En 2009-2013 las dos coinciden con 0,000%, así que
-- el empalme del histórico con Superset NO produce escalón de nivel.
--
-- `provisorio`, no `definitivo`, a propósito: el número se revisa hacia atrás cuando los
-- productores rectifican, y así queda dicho.
create or replace view etl_hidrocarburos_actual as
select distinct on (serie, date)
    serie, date, valor, estado, fuente, ingested_at
from etl_hidrocarburos
where estado is distinct from 'desestacionalizado'
order by serie, date,
         (case when estado = 'provisorio' then 0 when estado is null then 1 else 2 end),
         ingested_at desc;

-- Serie desestacionalizada (X-13), un valor por (serie, mes).
create or replace view etl_hidrocarburos_desest as
select distinct on (serie, date)
    serie, date, valor, fuente, ingested_at, parametros
from etl_hidrocarburos
where estado = 'desestacionalizado'
order by serie, date, ingested_at desc;
