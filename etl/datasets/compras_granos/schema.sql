-- Compras de granos del sector exportador / industria y DJVE (MAGyP), serie SEMANAL.
-- Formato LONG: 1 fila por (cultivo, cosecha, sector, metrica, date). `date` es la fecha de
-- corte del informe, NO el primer día del mes. Unidad: miles de toneladas.
--
-- Modelo append-only, igual que el resto del repo: cada corrida inserta un snapshot nuevo con su
-- ingested_at y nunca pisa un dato. La vista _actual devuelve el último snapshot de cada clave,
-- así una revisión posterior de MAGyP sobre una semana ya publicada aparece sin perder la
-- versión anterior.
--
-- No entra a series_actual / series_desest: esas vistas son mensuales y esta serie es semanal y
-- mezcla flujos con acumulados de campaña (no se desestacionaliza).

create table if not exists etl_compras_granos (
    id          bigint generated always as identity primary key,
    cultivo     text   not null check (cultivo in
                  ('trigo','maiz','sorgo','cebada_cervecera','cebada_forrajera','soja','girasol')),
    cosecha     text   not null,              -- campaña comercial, tal como la publica la fuente ('25/26')
    sector      text   not null check (sector in ('exportador','industria','total')),
    metrica     text   not null check (metrica in
                  ('semanal','total_comprado','precio_hecho','a_fijar','fijado','saldo_a_fijar',
                   'djve_acum','embarque_estimado','ventas_potenciales','ventas_efectivas',
                   'compras_estimadas','compras_declaradas')),
    date        date   not null,              -- fecha de corte del informe semanal
    valor       double precision,             -- miles de toneladas
    -- Fecha de corte PROPIA del bloque cuando difiere de `date`: el bloque industria suele venir
    -- atrasado respecto del encabezado (p.ej. "AL 27/05/2026" dentro del informe del 01/07/2026).
    -- Sin esta columna, la industria parece repetir valores semana a semana sin explicación.
    corte       date,
    estado      text,                         -- 'definitivo' (única fuente: el HTML semanal)
    fuente      text,                         -- URL de la página semanal
    ingested_at timestamptz not null default now()
);

-- Upgrade idempotente para bases creadas antes de que existiera la columna.
alter table etl_compras_granos add column if not exists corte date;

-- Búsqueda del último snapshot de una clave (la usa insert_if_changed en cada corrida).
create index if not exists etl_compras_granos_clave_idx
    on etl_compras_granos (cultivo, cosecha, sector, metrica, date, estado, ingested_at desc);

-- Recorrido por fecha, que es como se consulta la serie desde la app.
create index if not exists etl_compras_granos_date_idx
    on etl_compras_granos (date, cultivo, metrica);

-- Serie "actual": último snapshot de cada (cultivo, cosecha, sector, metrica, date).
create or replace view etl_compras_granos_actual as
select distinct on (cultivo, cosecha, sector, metrica, date)
    cultivo, cosecha, sector, metrica, date, valor, corte, estado, fuente, ingested_at
from etl_compras_granos
order by cultivo, cosecha, sector, metrica, date, ingested_at desc;
