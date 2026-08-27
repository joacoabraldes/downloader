-- Ventas al mercado interno de derivados del petroleo (Secretaria de Energia), formato LONG.
-- Es la contracara de `hidrocarburos`: aquel mide produccion, este mide comercializacion.
--
-- Star-schema, igual que comex y datos_gob:
--   etl_ventas_combustibles         hechos  (serie, date, valor)
--   etl_ventas_combustibles_series  dimension (producto, unidad, tipo, familia)
--
-- La dimension es el punto del diseno: permite armar CUALQUIER total con un `where` en vez de
-- congelarlo al ingestar. Se guardan los 51 productos, no un total precalculado, porque el
-- agregado se deriva y el grano no: recuperar el grano despues es un backfill de 200 meses.
--
-- `serie` NO tiene check ni FK, a proposito y por el mismo motivo que comex: si la Secretaria
-- abre un producto nuevo, la ingesta NO se rompe. El producto entra igual y aparece con la
-- dimension en NULL, que es la senal de curar el catalogo. Ademas el run lo registra como FALLA
-- para que la corrida salga con codigo != 0 y avise: un refinado nuevo sin clasificar quedaria
-- fuera de los totales y los subestimaria en silencio.
--
-- OJO: HAY CRUDO ADENTRO DEL DATASET DE VENTAS. 15 de los 51 "productos" no son refinados, son
-- petroleo crudo por cuenca mas `Crudo importado`, y entran en el total que muestra el chart
-- oficial de la fuente: hasta 6,6% del total en 2010, ~1,5% en 2022, 0 desde 2025-01. Se guardan
-- (son un dato) marcados tipo='crudo' y quedan FUERA de todos los totales de este schema. Tomar
-- el total del chart tal cual deja la serie 2010-2024 inflada y con una baja tendencial falsa,
-- producida por la desaparicion del crudo y no por el consumo.
--
-- TRES UNIDADES Y NO SE SUMAN ENTRE SI: (m3) los liquidos, (Ton) los pesados y el GLP,
-- (miles/m3) los gaseosos. La unidad vive en la dimension y TODOS los totales filtran por ella.
--
-- Modelo append-only: cada corrida inserta un snapshot nuevo con su ingested_at; nunca se pisa.

create table if not exists etl_ventas_combustibles (
  id          bigint generated always as identity primary key,
  serie       text   not null,             -- slug del producto, ej. 'gasoil_g2_comun'
  date        date   not null,             -- primer dia del mes
  valor       double precision,            -- unidad SEGUN la dimension (m3 / Ton / miles de m3)
  estado      text,                        -- definitivo (Superset) / desestacionalizado (X-13)
  fuente      text,
  parametros  jsonb,                       -- solo en desest
  ingested_at timestamptz not null default now()
);

create index if not exists etl_ventas_combustibles_serie_date_estado_idx
  on etl_ventas_combustibles (serie, date, estado, ingested_at desc);

create unique index if not exists etl_ventas_combustibles_desest_uq
  on etl_ventas_combustibles (serie, date)
  where estado = 'desestacionalizado';

-- Dimension: desarma el slug. `tipo` separa refinado / crudo / gaseoso y `familia` agrupa los
-- refinados para poder armar totales sin enumerar productos uno por uno.
create table if not exists etl_ventas_combustibles_series (
  serie    text primary key,
  nombre   text not null,
  unidad   text not null,      -- '(m3)' | '(Ton)' | '(miles/m3)'
  tipo     text not null,      -- 'refinado' | 'crudo' | 'gaseoso'
  familia  text not null,      -- gasoil | nafta | aviacion | glp | pesados | lubricantes | solventes | otros | gas | crudo
  orden    integer
);

insert into etl_ventas_combustibles_series (serie, nombre, unidad, tipo, familia, orden) values
  ('aerokerosene', 'Aerokerosene (jet)', '(m3)', 'refinado', 'aviacion', 1),
  ('aeronaftas', 'Aeronaftas', '(m3)', 'refinado', 'aviacion', 2),
  ('aguarras', 'Aguarras', '(m3)', 'refinado', 'solventes', 3),
  ('asfaltos', 'Asfaltos', '(Ton)', 'refinado', 'pesados', 4),
  ('bases_lubricantes', 'Bases lubricantes', '(m3)', 'refinado', 'lubricantes', 5),
  ('butano_c4', 'Butano y otros C4', '(Ton)', 'refinado', 'glp', 6),
  ('coque', 'Coque', '(Ton)', 'refinado', 'pesados', 7),
  ('crudo_austral_scruz_off', 'Crudo Cuenca Austral - Santa Cruz off shore', '(m3)', 'crudo', 'crudo', 8),
  ('crudo_austral_scruz_on', 'Crudo Cuenca Austral - Santa Cruz on shore', '(m3)', 'crudo', 'crudo', 9),
  ('crudo_austral_tdf_off', 'Crudo Cuenca Austral - Tierra del Fuego off shore (Hidra)', '(m3)', 'crudo', 'crudo', 10),
  ('crudo_austral_tdf_on', 'Crudo Cuenca Austral - Tierra del Fuego on shore (San Sebastian)', '(m3)', 'crudo', 'crudo', 11),
  ('crudo_cuyana', 'Crudo Cuenca Cuyana y Bolsones', '(m3)', 'crudo', 'crudo', 12),
  ('crudo_gsj_chubut', 'Crudo Cuenca Golfo San Jorge - Chubut (Escalante)', '(m3)', 'crudo', 'crudo', 13),
  ('crudo_gsj_scruz', 'Crudo Cuenca Golfo San Jorge - Santa Cruz (Canadon Seco)', '(m3)', 'crudo', 'crudo', 14),
  ('crudo_importado', 'Crudo importado', '(m3)', 'crudo', 'crudo', 15),
  ('crudo_neuquina_lapampa', 'Crudo Cuenca Neuquina - La Pampa (Medanito)', '(m3)', 'crudo', 'crudo', 16),
  ('crudo_neuquina_mendoza', 'Crudo Cuenca Neuquina - Mendoza', '(m3)', 'crudo', 'crudo', 17),
  ('crudo_neuquina_neuquen', 'Crudo Cuenca Neuquina - Neuquen (Medanito)', '(m3)', 'crudo', 'crudo', 18),
  ('crudo_neuquina_rionegro', 'Crudo Cuenca Neuquina - Rio Negro (Medanito)', '(m3)', 'crudo', 'crudo', 19),
  ('crudo_noroeste_formosa', 'Crudo Cuenca Noroeste - Formosa', '(m3)', 'crudo', 'crudo', 20),
  ('crudo_noroeste_jujuy', 'Crudo Cuenca Noroeste - Jujuy', '(m3)', 'crudo', 'crudo', 21),
  ('crudo_noroeste_salta', 'Crudo Cuenca Noroeste - Salta', '(m3)', 'crudo', 'crudo', 22),
  ('destilado_vacio', 'Destilado de vacio', '(m3)', 'refinado', 'pesados', 23),
  ('diesel_oil', 'Diesel oil', '(m3)', 'refinado', 'otros', 24),
  ('fueloil', 'Fueloil', '(Ton)', 'refinado', 'pesados', 25),
  ('gas_natural', 'Gas natural', '(miles/m3)', 'gaseoso', 'gas', 26),
  ('gas_refineria', 'Gas de refineria', '(miles/m3)', 'gaseoso', 'gas', 27),
  ('gasoil_g1_agro', 'Gasoil grado 1 (agrogasoil)', '(m3)', 'refinado', 'gasoil', 28),
  ('gasoil_g2_comun', 'Gasoil grado 2 (comun)', '(m3)', 'refinado', 'gasoil', 29),
  ('gasoil_g3_ultra', 'Gasoil grado 3 (ultra)', '(m3)', 'refinado', 'gasoil', 30),
  ('gasoil_otros', 'Otros tipos de gasoil', '(m3)', 'refinado', 'gasoil', 31),
  ('gasolina_natural', 'Gasolina natural', '(m3)', 'refinado', 'otros', 32),
  ('gnl', 'Gas natural licuado', '(miles/m3)', 'gaseoso', 'gas', 33),
  ('grasas', 'Grasas', '(Ton)', 'refinado', 'lubricantes', 34),
  ('kerosene', 'Kerosene', '(m3)', 'refinado', 'otros', 35),
  ('lubricantes_automotrices', 'Lubricantes automotrices', '(m3)', 'refinado', 'lubricantes', 36),
  ('lubricantes_industriales', 'Lubricantes industriales', '(m3)', 'refinado', 'lubricantes', 37),
  ('lubricantes_marinos', 'Lubricantes marinos', '(m3)', 'refinado', 'lubricantes', 38),
  ('mezclas_ifo', 'Mezclas IFO', '(Ton)', 'refinado', 'pesados', 39),
  ('nafta_g1_comun', 'Nafta grado 1 (comun)', '(m3)', 'refinado', 'nafta', 40),
  ('nafta_g2_super', 'Nafta grado 2 (super)', '(m3)', 'refinado', 'nafta', 41),
  ('nafta_g3_ultra', 'Nafta grado 3 (ultra)', '(m3)', 'refinado', 'nafta', 42),
  ('nafta_otros', 'Otros tipos de naftas', '(m3)', 'refinado', 'nafta', 43),
  ('nafta_virgen', 'Nafta virgen', '(m3)', 'refinado', 'solventes', 44),
  ('otros_livianos', 'Otros productos livianos', '(m3)', 'refinado', 'otros', 45),
  ('otros_medianos', 'Otros productos medianos', '(m3)', 'refinado', 'otros', 46),
  ('otros_pesados', 'Otros productos pesados', '(m3)', 'refinado', 'pesados', 47),
  ('propano_c3', 'Propano y otros C3', '(Ton)', 'refinado', 'glp', 48),
  ('solventes_alifaticos', 'Solventes alifaticos', '(m3)', 'refinado', 'solventes', 49),
  ('solventes_aromaticos', 'Solventes aromaticos', '(m3)', 'refinado', 'solventes', 50),
  ('solventes_hexano', 'Solventes hexano', '(m3)', 'refinado', 'solventes', 51)
on conflict (serie) do update set
  nombre = excluded.nombre, unidad = excluded.unidad,
  tipo = excluded.tipo, familia = excluded.familia, orden = excluded.orden;

-- Serie observada "actual" por (serie, mes), con la dimension pegada por JOIN. Un producto que
-- todavia no esta en el catalogo aparece igual, con nombre/unidad/tipo en NULL.
create or replace view etl_ventas_combustibles_actual as
select distinct on (c.serie, c.date)
       c.serie, c.date, c.valor, c.estado, c.fuente, c.ingested_at,
       d.nombre, d.unidad, d.tipo, d.familia
  from etl_ventas_combustibles c
  left join etl_ventas_combustibles_series d on d.serie = c.serie
 where c.estado is distinct from 'desestacionalizado'
 order by c.serie, c.date, c.ingested_at desc;

-- LOS TOTALES, definidos UNA sola vez y aca. Cada uno filtra por unidad ademas de por familia:
-- sumar m3 con toneladas no da nada.
--
--   total_automotor      gasoil + nafta, el consumo de surtidor. Excluye aviacion (el jet no es
--                        automotor) y excluye solventes, donde vive la nafta virgen, que es
--                        materia prima petroquimica y no se quema en un motor.
--   gasoil / nafta       cada familia por separado, sumando sus grados.
--   total_refinados_m3   los 26 refinados que se miden en m3 (de los 33 refinados, 7 van por
--                        peso). NO incluye crudo ni gaseosos.
--   total_refinados_ton  los refinados que se venden por peso (fueloil, coque, asfaltos, GLP...).
--   glp                  butano + propano, en toneladas.
create or replace view etl_ventas_combustibles_totales as
with base as (
  select date, valor, unidad, tipo, familia from etl_ventas_combustibles_actual
)
select 'total_automotor'::text as serie, date, sum(valor) as valor, '(m3)'::text as unidad
  from base where unidad = '(m3)' and familia in ('gasoil','nafta') group by date
union all
select 'gasoil', date, sum(valor), '(m3)'
  from base where unidad = '(m3)' and familia = 'gasoil' group by date
union all
select 'nafta', date, sum(valor), '(m3)'
  from base where unidad = '(m3)' and familia = 'nafta' group by date
union all
select 'total_refinados_m3', date, sum(valor), '(m3)'
  from base where unidad = '(m3)' and tipo = 'refinado' group by date
union all
select 'total_refinados_ton', date, sum(valor), '(Ton)'
  from base where unidad = '(Ton)' and tipo = 'refinado' group by date
union all
select 'glp', date, sum(valor), '(Ton)'
  from base where unidad = '(Ton)' and familia = 'glp' group by date;

-- Serie desestacionalizada (X-13). Trae los AGREGADOS por familia, no los 51 productos: se
-- ajustan `gasoil`, `nafta` y `glp` (ver etl/series_desest.toml), que se calculan sobre la vista
-- de totales.
--
-- `total_automotor` NO se ajusta directo: se DERIVA sumando gasoil + nafta (ajuste INDIRECTO).
-- Las dos componentes tienen estacionalidad OPUESTA -- nafta pica en verano (vacaciones) y gasoil
-- en primavera (cosecha) -- y al sumarlas se cancelan. Correr X-13 sobre el agregado romperia la
-- identidad total_automotor = gasoil + nafta (mismo motivo por el que comex no ajusta su indice
-- de valor) y, con componentes que se cancelan de forma imperfecta, suele dejar estacionalidad
-- residual que el indirecto no deja. Se marca con parametros->>'metodo' = 'indirecto'.
create or replace view etl_ventas_combustibles_desest as
with directas as (
  select distinct on (serie, date)
         serie, date, valor, fuente, ingested_at, parametros
    from etl_ventas_combustibles
   where estado = 'desestacionalizado'
   order by serie, date, ingested_at desc
)
select serie, date, valor, fuente, ingested_at, parametros from directas
union all
-- El `having count(*) = 2` evita publicar un total con una sola componente presente: media suma
-- se veria como un derrumbe del consumo.
select 'total_automotor'::text, date, sum(valor),
       'suma indirecta (gasoil + nafta)'::text, max(ingested_at),
       jsonb_build_object('metodo', 'indirecto',
                          'componentes', jsonb_build_array('gasoil', 'nafta'))
  from directas
 where serie in ('gasoil', 'nafta')
 group by date
having count(*) = 2;
