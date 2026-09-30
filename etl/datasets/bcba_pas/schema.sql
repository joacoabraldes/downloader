-- Panorama Agrícola Semanal (Bolsa de Cereales de Buenos Aires), desde el tablero público de
-- Power BI de la BCBA. Formato LONG: 1 fila por (cultivo, campania, zona_id, variable).
-- Sin desest ni deflación. No entra a series_actual (grano campaña); sí a estimaciones_actual.
--
-- Append-only: el tablero pisa la foto de cada campaña todas las semanas; acá cada release se
-- compara contra el último snapshot de cada clave y sólo se inserta lo nuevo o revisado, con la
-- `fecha_datos` ("Datos al") en la que apareció. _actual = el de la fecha de datos más reciente.
-- "Lo que decía el PAS del día X": último snapshot con fecha_datos <= X de cada clave.
--
-- OJO al consumir:
--   * zona_id 80 = 'nacional' = SUMA de las 15 zonas PAS. zona_id 0 = 'actual' = la estimación
--     NACIONAL oficial que comunica la BCBA (redondeada; desde 2022/23). No son lo mismo (maíz
--     2025/26: 64,0 Mt oficial vs 63,57 Mt suma de zonas): no sumarlas ni fusionarlas.
--   * Unidades TAL COMO SE PUBLICAN: rinde_qq_ha en quintales/ha; produccion_t en toneladas (el
--     rótulo de la fuente dice "MTn" pero el valor está en t). La conversión a kg/ha la hace
--     estimaciones_actual, no esta tabla.
--   * *_al_dia_ha = hectáreas sembradas/cosechadas A LA FECHA (avance); sup_sembrada_ha es la
--     estimación de área de la campaña; sup_cosechable_ha = sembrada - perdida.
--   * Celda vacía en la fuente = no hay fila (no es 0).

create table if not exists etl_bcba_pas (
    id           bigint generated always as identity primary key,
    cultivo      text     not null,             -- soja, maiz, trigo, girasol, cebada, sorgo
    campania     text     not null check (campania ~ '^\d{4}/\d{2}$'),
    zona_id      smallint not null,             -- Id_Zona de la BCBA: 1-15, 80 nacional, 0 actual
    variable     text     not null,             -- ver bcba_pas/config.py VARIABLES
    valor        double precision,
    -- Contexto: se escribe con cada snapshot pero NO se compara para el dedup.
    zona         text,                          -- 'I'..'XV', 'nacional', 'actual'
    fecha_datos  date     not null,             -- "Datos al" del release en que apareció el valor
    last_refresh timestamptz,                   -- LastRefreshTime del modelo (UTC)
    estado       text,                          -- 'publicado'
    fuente       text,                          -- URL del tablero
    ingested_at  timestamptz not null default now()
);

create index if not exists etl_bcba_pas_clave_idx
    on etl_bcba_pas (cultivo, campania, zona_id, variable, estado,
                     fecha_datos desc, ingested_at desc);

-- Releases procesados (por LastRefreshTime). Un release sin cambios no escribe filas: sin este
-- registro la corrida no sabría que ya lo vio. Da también `ultimo_dato` (max fecha_datos).
create table if not exists etl_bcba_pas_releases (
    last_refresh timestamptz primary key,
    fecha_datos  date not null,
    modelo       text,                          -- displayName del modelo ('Dashboard_PAS_v.3.2')
    procesado_at timestamptz not null default now(),
    celdas       integer,                       -- filas de Histórico_PAS (cultivo x campaña x zona)
    filas        integer,                       -- filas long (celdas x variables no vacías)
    nuevos       integer,
    actualizados integer
);

create or replace view etl_bcba_pas_actual as
select distinct on (cultivo, campania, zona_id, variable)
    cultivo, campania, zona_id, zona, variable, valor, fecha_datos, last_refresh, estado, fuente,
    ingested_at
from etl_bcba_pas
order by cultivo, campania, zona_id, variable, fecha_datos desc, ingested_at desc;
