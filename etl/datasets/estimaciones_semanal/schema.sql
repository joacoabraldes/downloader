-- Avance de siembra y cosecha (MAGyP, Informe Semanal de Estimaciones Agrícolas, PDF).
-- Formato LONG: 1 fila por (cultivo, campania, zona_tipo, zona, fase, variable, fecha_corte).
-- Sin desest ni deflación. No entra a series_actual (grano semanal intermitente por cultivo).
--
-- Append-only: cada informe se compara contra el último snapshot de cada clave y sólo se inserta
-- lo nuevo o revisado. `date_informe` = informe en el que apareció ESE valor (no es clave; ver
-- estimaciones_semanal/config.py). _actual = último snapshot (date_informe desc).
--
-- OJO al consumir:
--   * Celda de % vacía en el PDF = NO hay fila (no es 0). coalesce(valor, 0) si hace falta.
--   * cultivo: soja = total; soja_1ra / soja_2da sólo en delegaciones y provincias sin
--     delegación. trigo = total; trigo_pan / trigo_fideo donde se desglosa. arroz = total; arroz_la (largo ancho) / arroz_lf (largo fino). No sumar el
--     total con sus partes.
--   * zona_tipo 'provincia' = subtotal provincial O provincia publicada sin delegaciones
--     (CATAMARCA, SALTA, ...). Delegaciones mostradas a nivel provincia ("CHACO (Charata)",
--     "STGO. ESTERO (Quimilí)") son 'delegacion' con su `provincia`.
--   * variable: area_sembrada_ha (cosecha), area_a_sembrar_ha (siembra: intención), avance_pct,
--     area_no_cosechada_ha / area_cosechada_ha (desde 2025/26; el layout 2023 no las trae).
--   * Las áreas tienen fecha_corte = fecha del informe; avance_pct, la de su semana.

create table if not exists etl_estimaciones_semanal (
    id           bigint generated always as identity primary key,
    cultivo      text not null,               -- maiz, soja, soja_1ra, arroz_lf, ...
    campania     text not null check (campania ~ '^\d{4}/\d{2}$'),
    zona_tipo    text not null check (zona_tipo in ('pais', 'provincia', 'delegacion')),
    zona         text not null,               -- slug: total_pais, buenos_aires, pigue, ...
    fase         text not null check (fase in ('siembra', 'cosecha')),
    variable     text not null,
    fecha_corte  date not null,
    valor        double precision,            -- ha o %
    -- Contexto: se escribe con cada snapshot pero NO se compara para el dedup.
    provincia    text,                        -- slug de la provincia de la zona (null en país)
    zona_fuente  text,                        -- rótulo tal como viene en el PDF
    date_informe date not null,               -- informe en el que apareció este valor
    estado       text,                        -- 'publicado'
    fuente       text,                        -- URL del PDF
    ingested_at  timestamptz not null default now()
);

create index if not exists etl_estimaciones_semanal_clave_idx
    on etl_estimaciones_semanal (cultivo, campania, zona_tipo, zona, fase, variable, fecha_corte,
                                 estado, date_informe desc, ingested_at desc);

-- PDFs procesados (por URL). Un informe sin cambios no escribe filas: sin este registro la
-- corrida lo volvería a bajar cada vez. Da también `ultimo_dato` a etl_control_salud.
create table if not exists etl_estimaciones_semanal_informes (
    url          text primary key,
    date_informe date not null,
    procesado_at timestamptz not null default now(),
    filas        integer,                     -- filas parseadas del PDF
    nuevos       integer,
    actualizados integer
);

create or replace view etl_estimaciones_semanal_actual as
select distinct on (cultivo, campania, zona_tipo, zona, fase, variable, fecha_corte)
    cultivo, campania, zona_tipo, zona, provincia, fase, variable, fecha_corte, valor,
    zona_fuente, date_informe, estado, fuente, ingested_at
from etl_estimaciones_semanal
order by cultivo, campania, zona_tipo, zona, fase, variable, fecha_corte,
         date_informe desc, ingested_at desc;
