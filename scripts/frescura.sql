-- Frescura de los ETLs: una fila por dataset con un flag listo para consumir desde una app.
--
--   psql -f scripts/frescura.sql        (o pegarla en la app; no toma parámetros)
--
-- QUE DETECTA: que el dato esté más viejo de lo que la fuente ya debería haber publicado.
-- QUE **NO** DETECTA: que el ETL haya dejado de correr. Las tablas son append-only con
-- `insert_if_changed`: una corrida que no encuentra cambios NO escribe nada, así que
-- `max(ingested_at)` es "último día que un valor cambió", no "último día que el ETL corrió".
-- Un ETL muerto hace tres meses se ve igual que uno que corre a diario sin novedad. Para eso
-- hace falta un heartbeat por corrida, que hoy no existe.
--
-- `plazo_dias` = días después de cerrado el mes en los que la fuente ya publicó. El mes exigido
-- es el último mes M tal que ya pasaron `plazo_dias` desde que M cerró. Calibrado con el
-- comportamiento observado, no con la ventana del cron (que es una estimación más laxa).

with plazo(dataset, plazo_dias) as (values
    ('cemento',         12),   -- AFCP publica los días 1-10 del mes siguiente
    ('automotriz',      12),   -- ADEFA idem
    ('patentamientos',  12),   -- SIOMAA idem
    ('granos',          22),   -- MAGyP cerca del día 20
    ('aves',            33),   -- MAGyP 20-31 (el fallback PDF suele adelantarlo)
    ('bovinos',         33),   -- MAGyP 20-31
    ('leche',           40),   -- MAGyP 20-31, a veces hasta el 10 del subsiguiente
    ('acero',           38),   -- CAA: junio-2026 salió el 30/07 (~30 días), margen a 38
    ('demanda_energia', 38)    -- CAMMESA ~1 mes, fecha impredecible
),
ultimo as (
    select dataset, max(date) as ultimo_dato, max(ingested_at) as ultimo_cambio
    from series_actual group by dataset
),
calc as (
    select u.dataset, u.ultimo_dato, u.ultimo_cambio, p.plazo_dias,
           -- último mes ya vencido: retrocedo `plazo_dias` y me quedo con el mes anterior a ese
           (date_trunc('month', current_date - p.plazo_dias * interval '1 day')
            - interval '1 month')::date as mes_exigido
    from ultimo u join plazo p using (dataset)
)
select dataset,
       ultimo_dato,
       mes_exigido,
       ultimo_cambio,
       case when ultimo_dato < mes_exigido then 'ATRASADO' else 'ok' end as estado
from calc

union all

-- Carril diario: BCRA publica diar_bas.xls con 1-3 días hábiles de rezago.
select 'reservas_pasivos',
       max(date),
       (current_date - 5)::date,
       max(ingested_at),
       case when max(date) < current_date - 5 then 'ATRASADO' else 'ok' end
from series_diarias_actual
where dataset = 'reservas_pasivos'

order by estado desc, dataset;
