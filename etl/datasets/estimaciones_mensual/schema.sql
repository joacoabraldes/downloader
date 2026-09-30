-- Superficie y producción NACIONAL estimadas (MAGyP, Informe Mensual de Estimaciones
-- Agrícolas, PDF). Formato LONG: 1 fila por (cultivo, campania, variable). Sin desest ni
-- deflación. No entra a series_actual.
--
-- Append-only: un valor que se repite entre informes no genera fila; una revisión sí, con el
-- `date_informe` en el que apareció. _actual = el del informe más reciente.
--
-- OJO al consumir:
--   * variable: superficie_implantada_ha, superficie_a_implantar_ha (intención, antes de
--     terminar la siembra), produccion_t; maíz además superficie_grano_ha y
--     superficie_silaje_otros_ha (implantada = grano + silaje/diferidos/pérdida).
--   * Una campaña recién empezada tiene sólo superficie (la producción aparece más tarde).
--   * origen = 'campania_anterior': el valor salió de la columna "campaña anterior" de la
--     tabla de la campaña siguiente (p.ej. trigo 25/26 en septiembre de 2026).
--   * Maní en caja; algodón en bruto (como los publica MAGyP).

create table if not exists etl_estimaciones_mensual (
    id           bigint generated always as identity primary key,
    cultivo      text not null,               -- trigo, cebada, girasol, maiz, soja, sorgo, ...
    campania     text not null check (campania ~ '^\d{4}/\d{2}$'),
    variable     text not null,
    valor        double precision,            -- ha o t
    -- Contexto: se escribe con cada snapshot pero NO se compara para el dedup.
    origen       text,                        -- 'estimacion' | 'campania_anterior'
    etiqueta     text,                        -- rótulo de la fila en el PDF
    date_informe date not null,
    estado       text,                        -- 'publicado'
    fuente       text,                        -- URL del PDF
    ingested_at  timestamptz not null default now()
);

create index if not exists etl_estimaciones_mensual_clave_idx
    on etl_estimaciones_mensual (cultivo, campania, variable, estado,
                                 date_informe desc, ingested_at desc);

create table if not exists etl_estimaciones_mensual_informes (
    url          text primary key,
    date_informe date not null,
    procesado_at timestamptz not null default now(),
    filas        integer,
    nuevos       integer,
    actualizados integer
);

create or replace view etl_estimaciones_mensual_actual as
select distinct on (cultivo, campania, variable)
    cultivo, campania, variable, valor, origen, etiqueta, date_informe, estado, fuente,
    ingested_at
from etl_estimaciones_mensual
order by cultivo, campania, variable, date_informe desc, ingested_at desc;
