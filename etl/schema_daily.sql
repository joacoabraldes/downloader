-- Vista unificada de los datasets DIARIOS. Misma forma que series_actual (dataset, serie, date,
-- valor, estado, fuente, ingested_at) pero para series diarias. Está SEPARADA a propósito de
-- series_actual (mensual): acá `date` es la fecha diaria real, no el primer día del mes, así que
-- no se mezclan frecuencias. Los nombres legibles viven en etl_reservas_pasivos_actual.
--
-- Depende de las vistas *_actual de los datasets diarios, así que init-db la aplica al final del
-- carril diario. Por ahora el único dataset diario es reservas_pasivos; sumar más = otro `union all`.

create or replace view series_diarias_actual as
  select 'reservas_pasivos'::text as dataset, cd_serie as serie, date, valor, estado, fuente, ingested_at
    from etl_reservas_pasivos_actual;
