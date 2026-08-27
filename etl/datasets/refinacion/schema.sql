-- INSUMOS procesados por las refinerias del pais (Secretaria de Energia), formato LONG.
--
-- OJO CON EL NOMBRE DE LA FUENTE: el dashboard se llama "Productos procesados" y son INSUMOS,
-- no productos terminados. Lo que SALE de la refineria esta en otro dataset de la fuente (el 74,
-- dashboard 97 "Subproductos obtenidos"). Se distingue por los conceptos: aca dicen
-- 'Cuenca Neuquina - Neuquen (Medanito)' y 'Biodiesel'; alla dicen 'Gasoil Grado 2 (Comun)'.
--
-- Star-schema, igual que ventas_combustibles:
--   etl_refinacion         hechos    (serie, date, valor)
--   etl_refinacion_series  dimension (nombre, tipo, familia)
--
-- `serie` NO tiene check ni FK: si la fuente abre una corriente nueva la ingesta no se rompe --
-- entra con la dimension en NULL y el run lo reporta como falla. Mismo criterio que comex y
-- ventas_combustibles.
--
-- CRUDO PROCESADO != TOTAL PROCESADO, y la brecha crece. El crudo era el 85,5% de lo procesado en
-- 2010 y es el 78,9% en julio-2026; la diferencia la explican los biocombustibles, que crecieron
-- con los cortes obligatorios. Usar el total como si fuera crudo procesado sobreestima, y cada
-- anio un poco mas. Por eso son series distintas y las dos estan.
--
-- Unidad unica: METROS CUBICOS. La fuente trae ademas `cantidadtoneladas` para los mismos
-- conceptos; NO se ingesta, porque mezclar dos medidas de lo mismo en la columna `valor` es
-- exactamente la trampa que tiene ventas_combustibles.
--
-- Modelo append-only: cada corrida inserta un snapshot nuevo; nunca se pisa un dato.

create table if not exists etl_refinacion (
  id          bigint generated always as identity primary key,
  serie       text   not null,             -- slug del concepto, ej. 'crudo_neuquina_neuquen'
  date        date   not null,             -- primer dia del mes
  valor       double precision,            -- METROS CUBICOS (unidad unica del dataset)
  estado      text,                        -- definitivo (Superset) / desestacionalizado (X-13)
  fuente      text,
  parametros  jsonb,
  ingested_at timestamptz not null default now()
);

create index if not exists etl_refinacion_serie_date_estado_idx
  on etl_refinacion (serie, date, estado, ingested_at desc);

create unique index if not exists etl_refinacion_desest_uq
  on etl_refinacion (serie, date)
  where estado = 'desestacionalizado';

-- Dimension. Para el crudo, `familia` es la CUENCA de origen: sale gratis y deja ver el
-- corrimiento de la dieta de las refinerias hacia la Neuquina (Vaca Muerta).
create table if not exists etl_refinacion_series (
  serie    text primary key,
  nombre   text not null,
  tipo     text not null,      -- 'crudo' | 'otro_insumo'
  familia  text not null,      -- crudo: neuquina|golfo_san_jorge|austral|noroeste|cuyana|importado
                               -- otros: biocombustible|corte|nafta_mejorador|otros
  orden    integer
);

insert into etl_refinacion_series (serie, nombre, tipo, familia, orden) values
  ('crudo_austral_scruz_off', 'Crudo Cuenca Austral - Santa Cruz off shore', 'crudo', 'austral', 1),
  ('crudo_austral_scruz_on', 'Crudo Cuenca Austral - Santa Cruz on shore', 'crudo', 'austral', 2),
  ('crudo_austral_tdf_off', 'Crudo Cuenca Austral - Tierra del Fuego off shore (Hidra)', 'crudo', 'austral', 3),
  ('crudo_austral_tdf_sansebastian', 'Crudo Cuenca Austral - Tierra del Fuego (San Sebastian)', 'crudo', 'austral', 4),
  ('crudo_cuyana', 'Crudo Cuenca Cuyana y Bolsones', 'crudo', 'cuyana', 5),
  ('crudo_gsj_canadon', 'Crudo Cuenca Golfo San Jorge - Canadon Seco', 'crudo', 'golfo_san_jorge', 6),
  ('crudo_gsj_chubut', 'Crudo Cuenca Golfo San Jorge - Chubut (Escalante)', 'crudo', 'golfo_san_jorge', 7),
  ('crudo_importado', 'Crudo importado', 'crudo', 'importado', 8),
  ('crudo_neuquina_lapampa', 'Crudo Cuenca Neuquina - La Pampa (Medanito)', 'crudo', 'neuquina', 9),
  ('crudo_neuquina_mendoza', 'Crudo Cuenca Neuquina - Mendoza', 'crudo', 'neuquina', 10),
  ('crudo_neuquina_neuquen', 'Crudo Cuenca Neuquina - Neuquen (Medanito)', 'crudo', 'neuquina', 11),
  ('crudo_neuquina_rionegro', 'Crudo Cuenca Neuquina - Rio Negro (Medanito)', 'crudo', 'neuquina', 12),
  ('crudo_noroeste_formosa', 'Crudo Cuenca Noroeste - Formosa', 'crudo', 'noroeste', 13),
  ('crudo_noroeste_jujuy', 'Crudo Cuenca Noroeste - Jujuy', 'crudo', 'noroeste', 14),
  ('crudo_noroeste_salta', 'Crudo Cuenca Noroeste - Salta', 'crudo', 'noroeste', 15),
  ('biodiesel', 'Biodiesel', 'otro_insumo', 'biocombustible', 16),
  ('bioetanol', 'Bioetanol', 'otro_insumo', 'biocombustible', 17),
  ('cortes_fueloil', 'Cortes fuel oil', 'otro_insumo', 'corte', 18),
  ('cortes_gasoil', 'Cortes de gas oil', 'otro_insumo', 'corte', 19),
  ('cortes_kerosene', 'Cortes de kerosene', 'otro_insumo', 'corte', 20),
  ('cortes_nafta_virgen', 'Cortes de nafta virgen', 'otro_insumo', 'corte', 21),
  ('cortes_solventes', 'Cortes de solventes', 'otro_insumo', 'corte', 22),
  ('gasolina_natural', 'Gasolina natural', 'otro_insumo', 'nafta_mejorador', 23),
  ('mejoradores_otros', 'Otros mejoradores de octano', 'otro_insumo', 'nafta_mejorador', 24),
  ('mtbe', 'MTBE', 'otro_insumo', 'nafta_mejorador', 25),
  ('nafta_craqueo', 'Nafta de craqueo catalitico', 'otro_insumo', 'nafta_mejorador', 26),
  ('nafta_reformado', 'Nafta de reformado', 'otro_insumo', 'nafta_mejorador', 27),
  ('nafta_virgen', 'Nafta virgen', 'otro_insumo', 'nafta_mejorador', 28),
  ('naftas_otras', 'Otros tipos de naftas', 'otro_insumo', 'nafta_mejorador', 29),
  ('aditivos_lubricantes', 'Aditivos para lubricantes', 'otro_insumo', 'otros', 30),
  ('bases_lubricantes', 'Bases lubricantes', 'otro_insumo', 'otros', 31),
  ('condensado', 'Condensado', 'otro_insumo', 'otros', 32),
  ('destilado_vacio', 'Destilado de vacio', 'otro_insumo', 'otros', 33),
  ('gas_natural', 'Gas natural', 'otro_insumo', 'otros', 34),
  ('otros_productos', 'Otros productos', 'otro_insumo', 'otros', 35),
  ('residuo_destilacion', 'Residuo de destilacion', 'otro_insumo', 'otros', 36)
on conflict (serie) do update set
  nombre = excluded.nombre, tipo = excluded.tipo,
  familia = excluded.familia, orden = excluded.orden;

-- EL GRANO: un concepto por (serie, mes), con la dimension pegada.
create or replace view etl_refinacion_conceptos as
select distinct on (c.serie, c.date)
       c.serie, c.date, c.valor, c.estado, c.fuente, c.ingested_at,
       d.nombre, d.tipo, d.familia
  from etl_refinacion c
  left join etl_refinacion_series d on d.serie = c.serie
 where c.estado is distinct from 'desestacionalizado'
 order by c.serie, c.date, c.ingested_at desc;

-- LOS AGREGADOS.
--   crudo_procesado   los 15 conceptos de crudo. Es el "crude run" clasico y la serie que se sigue.
--   otros_insumos     los 21 restantes (biocombustibles, cortes, mejoradores).
--   total_procesado   todo lo que entro a refineria.
--   crudo_<cuenca>    el crudo abierto por cuenca de origen.
create or replace view etl_refinacion_totales as
with base as (select date, valor, tipo, familia, ingested_at from etl_refinacion_conceptos)
select 'crudo_procesado'::text as serie, date, sum(valor) as valor, max(ingested_at) as ingested_at
  from base where tipo = 'crudo' group by date
union all
select 'otros_insumos', date, sum(valor), max(ingested_at)
  from base where tipo = 'otro_insumo' group by date
union all
select 'total_procesado', date, sum(valor), max(ingested_at)
  from base group by date
union all
select 'crudo_' || familia, date, sum(valor), max(ingested_at)
  from base where tipo = 'crudo' group by date, familia;

-- Serie observada "actual": EL GRANO **MAS** LOS AGREGADOS, que es la forma del resto del repo
-- (ver la nota equivalente en ventas_combustibles/schema.sql: dejar los agregados afuera rompe el
-- join series_actual <-> series_desest). Las filas derivadas llevan tipo='agregado' y
-- estado='derivado'. Agregados y componentes conviven: sum(valor) sobre todo el dataset CUENTA
-- DOBLE y siempre hay que filtrar.
create or replace view etl_refinacion_actual as
select serie, date, valor, estado, fuente, ingested_at, nombre, tipo, familia
  from etl_refinacion_conceptos
union all
select t.serie, t.date, t.valor, 'derivado'::text,
       'suma de conceptos (ver etl_refinacion_series)'::text, t.ingested_at,
       case t.serie when 'crudo_procesado' then 'Crudo procesado'
                    when 'otros_insumos'   then 'Otros insumos (biocombustibles, cortes, mejoradores)'
                    when 'total_procesado' then 'Total procesado'
                    else 'Crudo procesado - cuenca ' || replace(substr(t.serie, 7), '_', ' ')
       end,
       'agregado'::text,
       case when t.serie in ('crudo_procesado','otros_insumos','total_procesado') then 'total'
            else substr(t.serie, 7) end
  from etl_refinacion_totales t;

-- Serie desestacionalizada (X-13). Hoy vacia: el dataset no esta en etl/series_desest.toml.
create or replace view etl_refinacion_desest as
select distinct on (serie, date)
       serie, date, valor, fuente, ingested_at, parametros
  from etl_refinacion
 where estado = 'desestacionalizado'
 order by serie, date, ingested_at desc;
