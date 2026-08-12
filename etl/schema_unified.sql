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
  select 'icc'::text as dataset, serie, date, valor, estado, fuente, ingested_at
    from etl_icc_actual
  union all
  select 'icg'::text as dataset, serie, date, valor, estado, fuente, ingested_at
    from etl_icg_actual;

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
  -- icc / icg no se desestacionalizan hoy: estas dos ramas devuelven 0 filas. Se dejan para
  -- que sumar X-13 más adelante sea sólo agregar el bloque en etl/series_desest.toml.
  select 'icc'::text as dataset, serie, date, valor, fuente, ingested_at, parametros
    from etl_icc_desest
  union all
  select 'icg'::text as dataset, serie, date, valor, fuente, ingested_at, parametros
    from etl_icg_desest;
