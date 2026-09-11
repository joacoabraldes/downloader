-- Vista unificada de los datasets DIARIOS. Misma forma que series_actual (dataset, serie, date,
-- valor, estado, fuente, ingested_at) pero para series diarias. Está SEPARADA a propósito de
-- series_actual (mensual): acá `date` es la fecha diaria real, no el primer día del mes, así que
-- no se mezclan frecuencias. Los nombres legibles viven en etl_reservas_pasivos_actual.
--
-- Depende de las vistas *_actual de los datasets diarios, así que init-db la aplica al final del
-- carril diario. Sumar un dataset diario = otro `union all`.
--
-- fob_granos entra por etl_fob_granos_diario y no por su *_actual: la tabla de hechos tiene
-- varias filas por (producto, día) —una por ventana de embarque de la curva forward— y acá hace
-- falta una sola serie por día. La vista ya resuelve cuál es el precio spot de cada día.

create or replace view series_diarias_actual as
  select 'reservas_pasivos'::text as dataset, cd_serie as serie, date, valor, estado, fuente, ingested_at
    from etl_reservas_pasivos_actual
  union all
  select 'fob_granos'::text as dataset, producto as serie, date, valor, estado, fuente, ingested_at
    from etl_fob_granos_diario;
