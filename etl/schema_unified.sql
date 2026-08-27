-- Vistas que homogeneizan el consumo de los datasets (granos / cemento / automotriz /
-- patentamientos) ahora que todos están en formato LONG. Agregan una columna `dataset` y
-- unen las vistas por dataset, así se consultan todos con la misma forma (dataset, serie,
-- date, valor, ...).
--
-- Dependen de las tablas y sus vistas *_actual / *_desest, así que init-db las aplica AL
-- FINAL (solo cuando se inicializan todos los datasets).

-- Serie observada actual de todos los datasets (último snapshot por serie/mes, sin desest).
create or replace view series_actual as
  select 'granos'::text     as dataset, serie, date, valor, estado, fuente, ingested_at
    from etl_molienda_granos_actual
  union all
  select 'cemento'::text    as dataset, serie, date, valor, estado, fuente, ingested_at
    from etl_cemento_despacho_actual
  union all
  select 'automotriz'::text as dataset, serie, date, valor, estado, fuente, ingested_at
    from etl_automotriz_actual
  union all
  select 'patentamientos'::text as dataset, serie, date, valor, estado, fuente, ingested_at
    from etl_patentamientos_actual
  union all
  select 'acero'::text as dataset, serie, date, valor, estado, fuente, ingested_at
    from etl_acero_actual
  union all
  select 'aves'::text as dataset, serie, date, valor, estado, fuente, ingested_at
    from etl_aves_actual
  union all
  select 'leche'::text as dataset, serie, date, valor, estado, fuente, ingested_at
    from etl_leche_actual
  union all
  select 'bovinos'::text as dataset, serie, date, valor, estado, fuente, ingested_at
    from etl_bovinos_actual
  union all
  select 'demanda_energia'::text as dataset, serie, date, valor, estado, fuente, ingested_at
    from etl_demanda_energia_actual
  union all
  -- hidrocarburos mezcla unidades entre sus series: `petroleo*` en miles de m3 y `gas*` en
  -- millones de m3. Ademas conviven el total y su desagregado por tipo de recurso, asi que
  -- sumar todas las series de este dataset cuenta doble: filtrar por serie in ('petroleo','gas').
  select 'hidrocarburos'::text as dataset, serie, date, valor, estado, fuente, ingested_at
    from etl_hidrocarburos_actual
  union all
  -- escrituras_caba es un CONTEO de actos, no una magnitud fisica ni un indice. Tiene 6 meses
  -- rellenados desde una planilla posterior (estado='relleno'), que se distinguen por esa columna.
  select 'escrituras_caba'::text as dataset, serie, date, valor, estado, fuente, ingested_at
    from etl_escrituras_caba_actual
  union all
  -- ventas_combustibles son 51 PRODUCTOS con TRES unidades distintas (m3 / Ton / miles de m3) y
  -- 15 de ellos son crudo, no refinados. Sumar todo este dataset no da nada: para los totales
  -- usar la vista etl_ventas_combustibles_totales, y para el grano etl_ventas_combustibles_actual,
  -- que trae la dimension (unidad, tipo, familia) pegada.
  select 'ventas_combustibles'::text as dataset, serie, date, valor, estado, fuente, ingested_at
    from etl_ventas_combustibles_actual
  union all
  select 'icc'::text as dataset, serie, date, valor, estado, fuente, ingested_at
    from etl_icc_actual
  union all
  select 'icg'::text as dataset, serie, date, valor, estado, fuente, ingested_at
    from etl_icg_actual
  union all
  -- datos_gob mezcla unidades entre sus series (indices, USD, pesos). El nombre y la unidad de
  -- cada una salen de etl_datos_gob_actual, que ya trae la dimension.
  select 'datos_gob'::text as dataset, serie, date, valor, estado, fuente, ingested_at
    from etl_datos_gob_actual
  union all
  -- comex son 18 numeros indice base 2004=100 (valor/precio/cantidad x rubro). El desarme del
  -- slug en flujo/indice/rubro esta en etl_comex_actual, que ya trae la dimension.
  select 'comex'::text as dataset, serie, date, valor, estado, fuente, ingested_at
    from etl_comex_actual;

-- Serie desestacionalizada (X-13) de todos los datasets, un valor por serie/mes.
-- `parametros` (jsonb) trae lo que se usó en la corrida X-13 (modo mult/add, metodo, etc.).
create or replace view series_desest as
  select 'granos'::text     as dataset, serie, date, valor, fuente, ingested_at, parametros
    from etl_molienda_granos_desest
  union all
  select 'cemento'::text    as dataset, serie, date, valor, fuente, ingested_at, parametros
    from etl_cemento_despacho_desest
  union all
  select 'automotriz'::text as dataset, serie, date, valor, fuente, ingested_at, parametros
    from etl_automotriz_desest
  union all
  select 'patentamientos'::text as dataset, serie, date, valor, fuente, ingested_at, parametros
    from etl_patentamientos_desest
  union all
  select 'acero'::text as dataset, serie, date, valor, fuente, ingested_at, parametros
    from etl_acero_desest
  union all
  select 'aves'::text as dataset, serie, date, valor, fuente, ingested_at, parametros
    from etl_aves_desest
  union all
  select 'leche'::text as dataset, serie, date, valor, fuente, ingested_at, parametros
    from etl_leche_desest
  union all
  select 'bovinos'::text as dataset, serie, date, valor, fuente, ingested_at, parametros
    from etl_bovinos_desest
  union all
  select 'demanda_energia'::text as dataset, serie, date, valor, fuente, ingested_at, parametros
    from etl_demanda_energia_desest
  union all
  -- Solo los dos totales se desestacionalizan: `petroleo` (miles de m3) y `gas` (millones de
  -- m3). El desagregado por tipo de recurso queda crudo.
  select 'hidrocarburos'::text as dataset, serie, date, valor, fuente, ingested_at, parametros
    from etl_hidrocarburos_desest
  union all
  -- escrituras_caba no se desestacionaliza: esta vista aporta 0 filas (igual que icc / icg).
  select 'escrituras_caba'::text as dataset, serie, date, valor, fuente, ingested_at, parametros
    from etl_escrituras_caba_desest
  union all
  -- ventas_combustibles no se desestacionaliza todavia: aporta 0 filas.
  select 'ventas_combustibles'::text as dataset, serie, date, valor, fuente, ingested_at, parametros
    from etl_ventas_combustibles_desest
  union all
  -- icc / icg no se desestacionalizan: estas dos ramas devuelven 0 filas. Se dejan para que
  -- sumar X-13 más adelante sea sólo agregar el bloque en etl/series_desest.toml.
  -- datos_gob SÍ aporta: las dos series de ventas, ajustadas sobre su serie real.
  select 'icc'::text as dataset, serie, date, valor, fuente, ingested_at, parametros
    from etl_icc_desest
  union all
  select 'icg'::text as dataset, serie, date, valor, fuente, ingested_at, parametros
    from etl_icg_desest
  union all
  select 'datos_gob'::text as dataset, serie, date, valor, fuente, ingested_at, parametros
    from etl_datos_gob_desest
  union all
  -- comex aporta las 6 series de cantidad (5 rubros de expo + nivel general de impo).
  select 'comex'::text as dataset, serie, date, valor, fuente, ingested_at, parametros
    from etl_comex_desest;
