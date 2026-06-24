-- Vistas que homogeneizan el consumo de las 3 series (granos / cemento / automotriz) ahora
-- que todas están en formato LONG. Agregan una columna `dataset` y unen las vistas por
-- dataset, así se consultan todas con la misma forma (dataset, serie, date, valor, ...).
--
-- Dependen de las 3 tablas y sus vistas *_actual / *_desest, así que init-db las aplica AL
-- FINAL (solo cuando se inicializan los 3 datasets).

-- Serie observada actual de todos los datasets (último snapshot por serie/mes, sin desest).
create or replace view series_actual as
  select 'granos'::text     as dataset, serie, date, valor, estado, fuente, ingested_at
    from molienda_granos_actual
  union all
  select 'cemento'::text    as dataset, serie, date, valor, estado, fuente, ingested_at
    from cemento_despacho_actual
  union all
  select 'automotriz'::text as dataset, serie, date, valor, estado, fuente, ingested_at
    from automotriz_actual;

-- Serie desestacionalizada (X-13) de todos los datasets, un valor por serie/mes.
create or replace view series_desest as
  select 'granos'::text     as dataset, serie, date, valor, fuente, ingested_at
    from molienda_granos_desest
  union all
  select 'cemento'::text    as dataset, serie, date, valor, fuente, ingested_at
    from cemento_despacho_desest
  union all
  select 'automotriz'::text as dataset, serie, date, valor, fuente, ingested_at
    from automotriz_desest;
