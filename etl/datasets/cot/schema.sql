-- Commitments of Traders (CFTC): posición especulativa en soja, maíz y trigo SRW de Chicago.
--
-- Dataset SEMANAL: `date` es la fecha de corte del reporte (martes). La CFTC publica los
-- viernes a las 15:30 ET con el corte del martes anterior.
-- Modelo append-only: cada corrida inserta un snapshot con su ingested_at; nunca pisa un dato.
-- Eso importa acá más que en otros datasets porque la CFTC REVISA semanas ya publicadas.

create table if not exists etl_cot (
  id              bigint generated always as identity primary key,
  contrato        text not null check (contrato in ('soja','maiz','trigo_srw')),
  categoria       text not null check (categoria in ('managed_money','non_commercial')),
  tipo            text not null check (tipo in ('futures','futures_options')),
  date            date not null,              -- fecha de corte del reporte (martes)
  largo           double precision,           -- posiciones largas, en contratos
  corto           double precision,           -- posiciones cortas, en contratos
  spreading       double precision,           -- posiciones de spread, en contratos
  interes_abierto double precision,           -- open interest total del contrato
  codigo_cftc     text,                       -- código de contrato del que vino la fila (005601 / 005602 / ...)
  unidad          text,                       -- 'contratos' (1998-01-06 ->) / 'miles_bushels' (antes)
  estado          text,                       -- 'publicado'
  fuente          text,                       -- URL del zip o de la consulta a la API
  ingested_at     timestamptz not null default now()
);

-- Upgrade idempotente para bases creadas antes de que existieran estas dos columnas.
alter table etl_cot add column if not exists codigo_cftc text;
alter table etl_cot add column if not exists unidad text;

create index if not exists etl_cot_clave_idx
  on etl_cot (contrato, categoria, tipo, date, estado, ingested_at desc);
create index if not exists etl_cot_date_idx on etl_cot (date);

comment on table etl_cot is
  'Commitments of Traders de la CFTC para los tres granos de Chicago: soja (código de contrato '
  '005602, antes 005601), maíz (002602 / 002601) y trigo SRW (001602 / 001601). Semanal, con '
  'fecha de corte los martes; la CFTC publica los viernes 15:30 ET. Las posiciones están en '
  'CONTRATOS, no en toneladas. '
  'Formato ancho: una fila por (contrato, categoria, tipo, fecha) con largo, corto, spreading e '
  'interes_abierto como columnas, porque las cuatro salen siempre juntas de la misma fila del '
  'archivo de la CFTC y se revisan juntas. '
  'Tabla append-only: la CFTC revisa semanas ya publicadas, así que una misma clave puede tener '
  'varios snapshots; el vigente es el de mayor ingested_at, ya resuelto en etl_cot_actual. Para '
  'la posición neta usar etl_cot_neto, que agrega neto = largo - corto.';

comment on column etl_cot.contrato is 'Grano: soja, maiz o trigo_srw (Chicago SRW; NO incluye HRW de Kansas ni HRSpring)';
comment on column etl_cot.categoria is
  'Qué grupo de operadores mide la fila, y salen de DOS reportes distintos que no se empalman. '
  '''managed_money'': reporte Disaggregated, desde 2006-06-13, fondos de gestión activa (CTAs, '
  'hedge funds de commodities). Es la categoría que sigue el mercado. '
  '''non_commercial'': reporte Legacy, desde 1986-01-15, categoría MÁS AMPLIA que incluye a los '
  'managed money y además a otros especuladores reportables. Es el único dato especulativo que '
  'existe antes de 2006, pero NO es la misma serie: pegarlas es una decisión de análisis.';
comment on column etl_cot.tipo is
  '''futures'': sólo futuros. ''futures_options'': futuros más opciones expresadas en '
  'equivalente futuro (delta-adjusted). El interes_abierto los distingue de un vistazo: soja al '
  '2026-09-08 da 1.070.401 contratos en futures y 1.378.220 en futures_options. La serie que se '
  'cita habitualmente en el mercado es futures.';
comment on column etl_cot.date is 'Fecha de corte del reporte, siempre un martes (NO la fecha de publicación, que es el viernes siguiente)';
comment on column etl_cot.largo is 'Posiciones largas de la categoría, en contratos';
comment on column etl_cot.corto is 'Posiciones cortas de la categoría, en contratos';
comment on column etl_cot.spreading is 'Posiciones de spread (largo y corto simultáneos en distintos vencimientos), en contratos. NO entran en el neto';
comment on column etl_cot.interes_abierto is 'Open interest total del contrato, no de la categoría. Es igual para las dos categorías de un mismo tipo: es el mismo contrato visto con dos particiones distintas';
comment on column etl_cot.codigo_cftc is 'Código de contrato del que vino la fila. Los tres granos cambiaron de código el 1998-01-06: 005601->005602 (soja), 002601->002602 (maíz), 001601->001602 (trigo)';
comment on column etl_cot.unidad is
  'Unidad del valor CRUDO de esta fila. ''contratos'' desde 1998-01-06; ''miles_bushels'' antes. '
  'Hasta 1997 la CFTC reportaba los granos en miles de bushels y desde 1998 en contratos de '
  '5.000 bushels, así que el tramo viejo está inflado por un factor de 5 y NO es comparable en '
  'nivel con el resto. La vista etl_cot_neto ya aplica la conversión; esta tabla guarda el crudo.';

-- Último snapshot por (contrato, categoria, tipo, fecha).
-- Los DROP son necesarios y no cosméticos: `create or replace view` en Postgres sólo permite
-- AGREGAR columnas al final, y estas dos vistas cambiaron el orden de sus columnas cuando se
-- sumaron codigo_cftc y unidad. Sin el drop, init-db falla en una base ya creada.
drop view if exists etl_cot_neto;
drop view if exists etl_cot_actual;

create or replace view etl_cot_actual as
select distinct on (contrato, categoria, tipo, date)
       contrato, categoria, tipo, date,
       largo, corto, spreading, interes_abierto,
       codigo_cftc, unidad, estado, fuente, ingested_at
from etl_cot
order by contrato, categoria, tipo, date, ingested_at desc;

comment on view etl_cot_actual is
  'Último snapshot por (contrato, categoria, tipo, fecha), con los valores CRUDOS tal como los '
  'publica la CFTC. Ojo: el tramo anterior al 1998-01-06 está en miles de bushels y no es '
  'comparable en nivel con el resto (ver la columna unidad). Para la serie ya homogénea usar '
  'etl_cot_neto.';

-- La vista de consumo: agrega la posición neta y su peso sobre el interés abierto.
create or replace view etl_cot_neto as
with f as (
  -- Factor que lleva todo a CONTRATOS: 1 desde 1998-01-06, 5 antes (los tres granos son
  -- contratos de 5.000 bushels y hasta 1997 la CFTC los reportaba en miles de bushels).
  select a.*,
         case when a.unidad = 'miles_bushels' then 5.0 else 1.0 end as divisor
  from etl_cot_actual a
)
select contrato, categoria, tipo, date,
       largo / divisor                                           as largo,
       corto / divisor                                           as corto,
       spreading / divisor                                       as spreading,
       interes_abierto / divisor                                 as interes_abierto,
       (largo - corto) / divisor                                 as neto,
       case when interes_abierto > 0
            then (largo - corto) / interes_abierto * 100 end     as neto_pct_oi,
       case when interes_abierto > 0
            then largo / interes_abierto * 100 end               as largo_pct_oi,
       case when interes_abierto > 0
            then corto / interes_abierto * 100 end               as corto_pct_oi,
       unidad                                                    as unidad_origen,
       divisor                                                   as divisor_aplicado,
       codigo_cftc, fuente, ingested_at
from f;

comment on view etl_cot_neto is
  'Commitments of Traders listo para consumir: posición NETA (largo - corto) y todo expresado en '
  'CONTRATOS, homogéneo en las cuatro décadas. '
  'CONVERSIÓN APLICADA, y conviene saberla: las filas anteriores al 1998-01-06 vienen de la '
  'CFTC en miles de bushels y acá se dividen por 5 (los tres contratos son de 5.000 bushels). '
  'Las columnas unidad_origen y divisor_aplicado dicen a qué filas se les tocó el valor, y '
  'etl_cot_actual conserva el crudo. Es una inferencia nuestra, no una conversión documentada '
  'por la CFTC, pero está medida: al cruzar el 1998-01-06 el interés abierto de los CUATRO '
  'contratos de granos del archivo cae en un factor de 4,65 a 5,51, mientras que contratos que '
  'no se miden en bushels no se mueven (boneless beef trimmings 1,05; electricidad CA-OR 0,75). '
  'Si fuera un cambio general de unidad de reporte, esos dos también saltarían. '
  'El spreading NO entra en el neto a propósito: son posiciones largas y cortas simultáneas en '
  'distintos vencimientos, que por construcción no expresan dirección. '
  'Las columnas *_pct_oi normalizan por el interés abierto y son lo que hay que usar para '
  'comparar entre granos o a lo largo de décadas: el contrato de soja pasó de ~80.000 a más de '
  'un millón de contratos abiertos, así que un neto de 50.000 no significa lo mismo en 1990 que '
  'hoy. '
  'DOS DISCONTINUIDADES que hay que conocer antes de graficar la serie larga de non_commercial: '
  '(1) la FRECUENCIA cambia — quincenal hasta 1991 (24 observaciones por año), transición en '
  '1992 (31) y semanal recién desde 1993; calcular variaciones semanales sobre el tramo viejo da '
  'cualquier cosa. (2) el CÓDIGO de contrato cambió en 1998 (005601 -> 005602 y equivalentes); '
  'la ingesta ya los unifica bajo el mismo `contrato` y el corte es limpio (último dato del '
  'código viejo 1997-12-30, primero del nuevo 1998-01-06, sin solape), pero el dato en sí viene '
  'de dos identificadores distintos de la fuente.';
