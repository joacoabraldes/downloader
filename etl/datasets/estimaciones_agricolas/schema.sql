-- Estimaciones agrícolas por departamento (MAGyP, Dirección de Estimaciones Agrícolas).
-- Formato WIDE: 1 fila por (cultivo, campania, departamento_id) con las 4 métricas publicadas.
-- Grano ANUAL (campaña agrícola, 1969/70 -> hoy). Sin desest y sin deflación: son cantidades
-- físicas anuales. No entra a series_actual / series_desest.
--
-- Modelo append-only, igual que el resto del repo: cada release de MAGyP se compara ENTERO contra
-- el último snapshot de cada clave (MAGyP revisa celdas viejas entre releases, hasta 1999) y sólo
-- se inserta lo que cambió. La vista _actual devuelve el último snapshot.
--
-- OJO al consumir:
--   * Un 0 puede ser "sin dato": los "SD" de releases viejos pasaron a 0. No hay forma de
--     distinguirlo de un cero real.
--   * No sumar agregados con sus partes: soja_total = soja_1ra + soja_2da; cebada_cervecera y
--     cebada_forrajera hasta 2015/16, cebada_total desde 2016/17; trigo_candeal es parte de
--     trigo_total; poroto_total = alubia + negro + otros (desglose 2021/22 -> 2024/25).
--   * `rendimiento` es el PUBLICADO (kg/ha), no se recalcula a partir de las otras columnas.
--   * Departamentos 'SIN DEFINIR' vienen con id PP000 (p.ej. '06000').

create table if not exists etl_estimaciones_agricolas (
    id                  bigint generated always as identity primary key,
    cultivo             text   not null,        -- slug estable mapeado por id (config.CULTIVOS)
    campania            text   not null check (campania ~ '^\d{4}/\d{2}$'),  -- '2025/26'
    departamento_id     text   not null check (departamento_id ~ '^\d{5}$'), -- INDEC: PP + DDD
    sup_sembrada        double precision,       -- ha
    sup_cosechada       double precision,       -- ha
    produccion          double precision,       -- t
    rendimiento         double precision,       -- kg/ha, tal como lo publica MAGyP
    -- Contexto: se escribe con cada snapshot pero NO se compara para el dedup.
    provincia_id        text,                   -- INDEC, 2 dígitos
    provincia           text,
    departamento        text,
    id_cultivo_fuente   integer,                -- 'Id Cultivo' de MAGyP
    fecha_actualizacion date,                   -- release en el que ESTE valor apareció
    estado              text,                   -- 'publicado' (única fuente)
    fuente              text,                   -- URL del portal
    ingested_at         timestamptz not null default now()
);

-- Búsqueda del último snapshot de una clave (insert_if_changed) y la vista _actual.
create index if not exists etl_estimaciones_agricolas_clave_idx
    on etl_estimaciones_agricolas (cultivo, campania, departamento_id, estado, ingested_at desc);

-- Releases procesados. Existe porque el dedup hace que un release SIN cambios no escriba ninguna
-- fila: sin este registro la corrida no sabría que ya lo vio y volvería a bajar los ~11 MB cada
-- semana. También da `ultimo_dato` para etl_control_salud (ver config.ULTIMO_DATO_SQL).
create table if not exists etl_estimaciones_agricolas_releases (
    fecha_actualizacion date primary key,       -- "Fecha de Actualización" de la página
    procesado_at        timestamptz not null default now(),
    filas               integer,                -- filas válidas del CSV
    nuevos              integer,
    actualizados        integer,
    fuente              text
);

-- Serie "actual": último snapshot de cada (cultivo, campania, departamento_id).
create or replace view etl_estimaciones_agricolas_actual as
select distinct on (cultivo, campania, departamento_id)
    cultivo, campania, departamento_id, provincia_id, provincia, departamento,
    sup_sembrada, sup_cosechada, produccion, rendimiento,
    id_cultivo_fuente, fecha_actualizacion, estado, fuente, ingested_at
from etl_estimaciones_agricolas
order by cultivo, campania, departamento_id, ingested_at desc;
