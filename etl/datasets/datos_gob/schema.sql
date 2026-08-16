-- Series de la API oficial del Estado (apis.datos.gob.ar/series), formato LONG.
-- Evaluación completa de la fuente: docs/datos_gob_ar.md
--
-- Modelo append-only: cada corrida inserta un snapshot con su ingested_at; nunca se pisa un
-- dato. Los organismos revisan meses ya publicados, así que `insert_if_changed` deja cada
-- revisión como snapshot nuevo y el anterior queda en la historia.
--
-- Todo entra con estado='definitivo': la API publica un solo dato por período.
--
-- Star-schema, igual que reservas_pasivos y por el mismo motivo: las series NO comparten
-- unidad (conviven índices, dólares y pesos en la misma tabla), así que el nombre legible y la
-- unidad viven en la dimensión y la vista _actual las une por JOIN.
--
-- `serie` no lleva CHECK ni FK a propósito (mismo criterio que reservas_pasivos): sumar una
-- serie es editar SERIES_META en config.py, y un CHECK acá obligaría a tocar dos lugares.

create table if not exists etl_datos_gob (
  id          bigint generated always as identity primary key,
  serie       text   not null,            -- slug propio; ver la dimensión
  date        date   not null,            -- primer día del mes
  valor       double precision,
  estado      text,                       -- 'definitivo' (API) / 'desestacionalizado' (X-13)
  fuente      text,                       -- URL de la serie en la API
  parametros  jsonb,                      -- solo en desest: parámetros de la corrida X-13
  ingested_at timestamptz not null default now()
);
alter table etl_datos_gob add column if not exists parametros jsonb;

create index if not exists etl_datos_gob_serie_date_estado_idx
  on etl_datos_gob (serie, date, estado, ingested_at desc);

create unique index if not exists etl_datos_gob_desest_uq
  on etl_datos_gob (serie, date)
  where estado = 'desestacionalizado';

-- Dimensión: nombre legible, unidad, organismo e id en la API.
--
-- A DIFERENCIA de reservas_pasivos, acá el seed NO se escribe en este archivo: lo upsertea
-- `run.py` en cada corrida desde `config.SERIES_META`, que es donde ya vive el id de la API
-- porque el ETL lo necesita para bajar la serie. Tener las filas también acá sería una segunda
-- copia de la misma verdad, condenada a desincronizarse.
create table if not exists etl_datos_gob_series (
  serie       text primary key,           -- slug propio ('isac', 'ipc_nacional', ...)
  id_api      text not null,              -- id en apis.datos.gob.ar ('33.2_ISAC_NIVELRAL_0_M_18_63')
  nombre      text not null,
  unidad      text,
  organismo   text,
  -- Índice con el que se deflacta la serie: 'ipc_largo' (pesos), 'uscpi_mensual' (dólares) o
  -- NULL si la serie no se deflacta. Es el nombre tal cual lo publica `public.deflactores`, y
  -- la vista _real lo usa para filtrar esa tabla. Fuente de verdad: SERIES_META en config.py.
  deflactor   text,
  -- DERIVADA de `deflactor` (`deflactor is not null`), la escribe `run.py`. Se conserva porque
  -- es la columna que `etl_datos_gob_actual` viene exponiendo y de esa vista cuelgan las
  -- unificadas de schema_unified.sql: sacarla obligaría a un `drop ... cascade`. No editarla a
  -- mano; el deflactor de una serie se cambia en config.py.
  deflactable boolean not null default false,  -- serie en valores corrientes: tiene versión real
  orden       int,
  -- Piso del valor real. NULL = sin piso (el caso normal). Sólo lo lleva `smvm`, cuya serie
  -- nominal cambia de moneda tres veces hacia atrás; ver REAL_DESDE en config.py.
  real_desde  date
);
-- Upgrades idempotentes para bases ya creadas sin estas columnas.
alter table etl_datos_gob_series add column if not exists deflactable boolean not null default false;
alter table etl_datos_gob_series add column if not exists real_desde date;
alter table etl_datos_gob_series add column if not exists deflactor text;

-- Serie observada "actual" por (serie, mes): último snapshot, enriquecida con la dimensión.
create or replace view etl_datos_gob_actual as
select distinct on (d.serie, d.date)
    d.serie, c.nombre, c.unidad, c.organismo, c.id_api,
    d.date, d.valor, d.estado, d.fuente, d.ingested_at,
    -- `deflactable` y `deflactor` van AL FINAL a propósito, y en ese orden: `create or replace
    -- view` sólo admite agregar columnas al final, nunca intercalarlas. Ponerlas en el medio
    -- rompe el re-apply del schema sobre una base ya creada (y habría que dropear la vista, de
    -- la que cuelgan etl_datos_gob_real y las unificadas de schema_unified.sql).
    c.deflactable, c.deflactor
from etl_datos_gob d
left join etl_datos_gob_series c on c.serie = d.serie
where d.estado is distinct from 'desestacionalizado'
order by d.serie, d.date, d.ingested_at desc;

-- Serie REAL: las series en valores corrientes llevadas a precios constantes.
--
--     real(t) = nominal(t) * indice(base) / indice(t)
--
-- DOS DEFLACTORES, uno por moneda, elegido POR SERIE por la columna `deflactor` de la dimensión
-- (fuente de verdad: SERIES_META en config.py):
--
--   'ipc_largo'      series en pesos corrientes (ventas, salarios).
--   'uscpi_mensual'  series en dólares corrientes (expo_total, impo_total). CPI-U del BLS,
--                    serie CUUR0000SA0, all items, NOT seasonally adjusted, desde 1913-01.
--
-- Estar en dólares NO exime de deflactar: un dólar de 1992 compra más que uno de 2026, así que
-- leer el nivel de expo/impo de los 90 contra el de hoy en dólares nominales sobrestima el
-- crecimiento por toda la inflación de EEUU del medio. Las dos series arrancan en 1992-01, o
-- sea 34 años de CPI acumulado: el ajuste no es cosmético.
--
-- El CPI que se usa es el NSA, y a propósito. El deflactor tiene que dejar la estacionalidad de
-- la serie intacta para que el X-13 posterior la mida entera: deflactar con la versión
-- desestacionalizada del CPI le metería la estacionalidad del deflactor dada vuelta, y el X-13
-- terminaría ajustando también ese artefacto nuestro.
--
-- Es una VISTA, no una tabla, por el mismo criterio con el que no guardamos la variación
-- mensual del ICC: es un derivado exacto de dos valores que ya están guardados, y guardarlo
-- sería una segunda copia de la misma verdad que puede desincronizarse.
--
-- BASE MÓVIL Y POR SERIE: cada serie se expresa en moneda de SU último dato observado. El mes
-- base sale del CTE `base`, que para cada serie toma el último mes que tiene a la vez dato
-- nominal y deflactor. No hay una fecha escrita en ningún lado: se calcula sola.
--
-- POR SERIE y no una sola para el dataset: las 11 deflactables terminan en meses distintos
-- (`smvm` publica antes que el IPC, los `indice_salarios_*` van más rezagados). Anclar todas al
-- mismo mes obligaría a elegir el más viejo o a extrapolar el resto. Cada serie en su propio
-- último dato. La columna `mes_base` dice en qué moneda está cada fila: dos series son
-- comparables directo sólo si comparten `mes_base` Y `deflactor`; si difiere el `mes_base` hay
-- que reescalar por indice(base_a)/indice(base_b), y si difiere el `deflactor` no se comparan
-- niveles sin pasar por un tipo de cambio (pesos constantes y dólares constantes son unidades
-- distintas, no dos escalas de la misma).
--
-- El deflactor entra COMPLETO, observado y proyectado por igual: `deflactores` empalma el IPC
-- publicado con las proyecciones encadenadas de `inflacion_proyectada`, y las proyecciones son
-- justamente lo que permite deflactar los meses que el IPC todavía no cubre (hoy, agosto-2026
-- de `smvm`). Los meses de deflactor POSTERIORES al último dato de una serie se descartan
-- solos: el JOIN es por fecha contra la serie observada.
--
-- CONSECUENCIA: cuando entra un mes nuevo de la serie, o cuando INDEC publica y se reancla la
-- proyección, esa serie real se reescala completa. Los niveles cambian, las variaciones no.
-- X-13 es invariante a escala, y por eso `run.py` recorre la desest en cada corrida sin
-- condicionarla a `rep.changed`.
--
-- OJO con el base proyectado: si el mes base de una serie tiene `origen = 'proyectado'` (hoy
-- `smvm`), corregir esa proyección mueve los niveles de TODA su historia, no sólo de ese mes.
-- Las variaciones siguen sin moverse.
--
-- POR QUÉ NO `ipc_nacional`: el IPC de este mismo dataset arranca en 2016-12 y dejaba a `ripte`
-- (nominal desde 1994) y `smvm` (desde 1965) sin valor real en casi toda su historia.
-- `ipc_largo` cubre desde 1990-01 y `uscpi_mensual` desde 1913-01.
--
-- DEPENDENCIA EXTERNA, y hay que saberla: `deflactores`, `ipc_largo` y `uscpi_mensual` viven en
-- esta misma base pero los mantiene OTRO repo (/home/jmt/dev/downloaders_viejos/downloader). El
-- matview `ipc_largo` lo refresca `IPCDownload.py` al final de cada corrida del ETL del IPC; el
-- CPI de EEUU lo baja `downloadUSCPI.py` del BLS. Dos consecuencias: (1) si esos ETLs se mueren,
-- esta vista devuelve datos viejos sin avisar; (2) igual que pasó con "IPCIndec", un DROP de
-- cualquiera de los dos sin CASCADE ahora falla, porque esta vista pasó a ser dependiente.
--
-- COLUMNA `origen`: procedencia del deflactor de ESE mes, tal como la publica `deflactores`.
--   'publicado'    el índice de ese mes ya salió.
--   'proyectado'   todavía no salió y el índice se encadenó desde una proyección cargada a mano
--                  en `inflacion_proyectada`. Hoy afecta sólo a `smvm`, que publica antes que
--                  el IPC. En dólares prácticamente no aparece: el BLS publica el CPI de un mes
--                  a mitad del siguiente, antes que el INDEC el comercio exterior de ese mes.
--   'interpolado'  sólo 'uscpi_mensual'. Mes que el BLS no publicó y se estimó por interpolación
--                  geométrica entre el anterior y el siguiente. Hoy es uno solo: octubre-2025,
--                  que se perdió por el shutdown. Cae dentro del rango de expo/impo.
-- Mirarla SIEMPRE antes de presentar un valor como firme: un mes proyectado se revisa cuando el
-- organismo publica, y uno interpolado es una cuenta nuestra, no un dato del BLS.
--
-- PRECISIÓN DEL TRAMO VIEJO: antes de 2016-12 `ipc_largo` se apoya en `inflaempalmada`, cuyo
-- índice es float4 y está redondeado a entero hasta 2023-09. El error de ±1 en el índice pesa
-- ~0,04% en 2016, ~0,2% en 2010 y ~0,66% en 1995-2002. Tolerable para niveles; para leer
-- variaciones mes a mes de los años 90, no.
--
-- `real_desde`: piso del valor real para las series cuyo NOMINAL no es homogéneo hacia atrás.
-- Hoy sólo `smvm` (1992-01), porque su monto viene en la moneda de cada época y esa moneda
-- cambió tres veces. Ver REAL_DESDE en config.py, que explica el porqué y por qué se recorta en
-- vez de convertir. Sin este filtro, 1990-1991 salía 10.000 veces más grande.
create or replace view etl_datos_gob_real as
with idx as (
    -- Los dos deflactores juntos, sin filtrar por nombre: quién usa cuál lo decide el JOIN de
    -- abajo contra `obs.deflactor`. Filtrar acá por un nombre fijo es lo que había antes, y era
    -- justamente lo que ataba todo el dataset a pesos.
    select deflactor, fecha as date, indice as valor, origen
    from public.deflactores
), obs as (
    -- Serie observada deflactable, con SU deflactor y ya recortada por `real_desde`. Se aísla
    -- en su propio CTE porque `base` y el select final tienen que ver EXACTAMENTE las mismas
    -- filas: si el mes base saliera de un universo más ancho que el que se deflacta, el mes
    -- base podría no existir en el resultado y `real = nominal` dejaría de valer en ninguna
    -- fila. El filtro es `c.deflactor is not null`, no la columna derivada `deflactable`: acá
    -- hace falta el nombre del índice igual, así que se usa la fuente y no su reflejo.
    select d.serie, d.nombre, d.unidad, d.date, d.valor, c.deflactor
    from etl_datos_gob_actual d
    join etl_datos_gob_series c on c.serie = d.serie
    where c.deflactor is not null
      and d.valor is not null
      and (c.real_desde is null or d.date >= c.real_desde)
), base as (
    -- Mes base DE CADA SERIE: su último mes con dato nominal y deflactor a la vez. El JOIN
    -- contra `idx` es lo que descarta los meses de deflactor posteriores al último dato de la
    -- serie (hoy `deflactores` llega a 2026-12 por proyecciones, y ninguna serie tanto).
    select distinct on (o.serie)
           o.serie, o.date as mes_base, i.valor as indice_base
    from obs o
    join idx i on i.deflactor = o.deflactor and i.date = o.date
    where i.valor > 0
    order by o.serie, o.date desc
)
select o.serie, o.nombre, o.unidad, o.date,
       o.valor * b.indice_base / i.valor as valor,
       o.valor as valor_nominal,
       b.mes_base,
       -- `origen` y `deflactor` van AL FINAL por lo mismo que `deflactable` en la vista de
       -- arriba: `create or replace view` sólo admite agregar columnas al final.
       i.origen,
       -- En qué moneda quedó el valor real de esta fila. Sin esto, `mes_base` sola es ambigua:
       -- dice el mes pero no si son pesos o dólares constantes.
       o.deflactor
from obs o
join idx i on i.deflactor = o.deflactor and i.date = o.date
join base b on b.serie = o.serie
where i.valor > 0;

-- Serie desestacionalizada (X-13), un valor por (serie, mes).
--
-- Se ajustan las dos series de ventas y las dos de comercio exterior, y se ajusta su serie
-- REAL, no la nominal: bajo inflación argentina la estacionalidad de una serie en pesos
-- corrientes queda tapada por la deriva de precios, así que desestacionalizar el nominal no
-- dice nada. En expo/impo la deriva es mucho menor (es CPI de EEUU), pero el orden correcto es
-- el mismo —deflactar y después ajustar— y además X-13 es invariante a escala, así que ajustar
-- la real no cuesta nada frente a ajustar la nominal. La vista insumo de X-13 es
-- etl_datos_gob_real (ver la clave `view` en etl/series_desest.toml).
--
-- El resto de las series no se ajusta acá. OJO: no es porque vengan ajustadas de origen. Los ids
-- que bajamos son las series ORIGINALES (el del IPI se llama literalmente "Serie Original"); para
-- varias de ellas INDEC publica la desestacionalizada como una serie APARTE, con su propio id.
-- Sumarla es agregar una fila a SERIES_META, no correr X-13. Ver etl/series_desest.toml.
create or replace view etl_datos_gob_desest as
select distinct on (serie, date)
    serie, date, valor, fuente, ingested_at, parametros
from etl_datos_gob
where estado = 'desestacionalizado'
order by serie, date, ingested_at desc;

-- Vista de consumo: los tres valores de cada serie/mes en una sola fila.
--   valor_nominal     como lo publica el organismo
--   valor_real        a precios del último dato de esa serie, que viaja en `mes_base`
--                     (NULL si la serie no se deflacta o el mes no tiene deflactor)
--   valor_desest      X-13 sobre la serie real (NULL si esa serie no se desestacionaliza)
--   mes_base          mes cuya moneda expresa `valor_real`
--   deflactor_origen  'publicado' | 'proyectado' | 'interpolado' para el mes de esa fila
--                     (NULL si no aplica). Todo lo que no sea 'publicado' se va a revisar
--                     cuando el organismo publique: no presentar esos meses como firmes.
--   deflactor         'ipc_largo' (pesos constantes) | 'uscpi_mensual' (dólares constantes).
--                     `mes_base` sin esto es ambigua: dice el mes, no la moneda.
create or replace view etl_datos_gob_completo as
select a.serie, a.nombre, a.unidad, a.organismo, a.date,
       a.valor as valor_nominal,
       r.valor as valor_real,
       s.valor as valor_desest,
       r.mes_base,
       r.origen as deflactor_origen,
       r.deflactor
from etl_datos_gob_actual a
left join etl_datos_gob_real   r on r.serie = a.serie and r.date = a.date
left join etl_datos_gob_desest s on s.serie = a.serie and s.date = a.date
order by a.serie, a.date;
