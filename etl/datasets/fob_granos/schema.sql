-- Precios FOB oficiales de granos (MAGyP) + las tablas de referencia del PRP (precio relativo
-- de los productos): derechos de exportación, pesos VBP y tipo de cambio del exportador.
--
-- El objetivo del bloque entero es reproducir en la base la planilla "TCR Granos.xlsx":
--
--   FOB REAL = FOB_usd_ton * TC_nominal / IPC(1993=1)
--   PRP      = FOB REAL * (1 - DEX)
--   PRP combinado = SUM(PRP_grano * VBP_grano)  sobre soja, trigo, maíz y girasol
--
-- Dataset DIARIO en la tabla de hechos (date = fecha de cotización real); los promedios
-- mensuales que consume el PRP salen de `etl_fob_granos_mensual`.
-- Modelo append-only: cada corrida inserta un snapshot con su ingested_at; nunca pisa un dato.

create extension if not exists btree_gist;   -- para los EXCLUDE de no-solapamiento de abajo

-- ---------------------------------------------------------------------------------------------
-- Hechos: precios FOB oficiales diarios
-- ---------------------------------------------------------------------------------------------

create table if not exists etl_fob_granos (
  id             bigint generated always as identity primary key,
  producto       text not null check (producto in ('soja','trigo','maiz','girasol')),
  date           date not null,              -- fecha de cotización (diaria real, NO primer día del mes)
  embarque_desde date not null,              -- primer mes de la ventana de embarque (primer día del mes)
  embarque_hasta date not null,              -- último mes de la ventana de embarque (primer día del mes)
  valor          double precision,           -- precio FOB en USD por tonelada
  posicion       text,                       -- posición arancelaria NCM+SIM+DV consultada
  circular       text,                       -- circular (resolución) que fijó el precio
  estado         text,                       -- 'oficial'
  fuente         text,                       -- URL de la API con la fecha consultada
  ingested_at    timestamptz not null default now()
);

create index if not exists etl_fob_granos_prod_date_idx
  on etl_fob_granos (producto, date, embarque_desde, embarque_hasta, estado, ingested_at desc);
create index if not exists etl_fob_granos_date_idx on etl_fob_granos (date);

comment on table etl_fob_granos is
  'Precios FOB oficiales diarios de granos publicados por MAGyP (Secretaría de Bioeconomía), en '
  'USD por tonelada. Fuente: API pública precios_fob.php, un request por día hábil, histórico '
  'disponible desde 1993-01-04. Se ingestan las cuatro posiciones arancelarias del TCR de granos, '
  'todas en la variante "a granel con hasta un 15 % embolsado": soja 1201-90-00 (12019000190C), '
  'trigo pan 1001-99-00 (10019900110W), maíz 1005-90-10 (10059010190Y) y girasol para industria '
  '1206-00-90 (12060090910Y). Cada día la fuente publica una curva forward: varias filas por '
  'posición con distinta ventana de embarque (embarque_desde/embarque_hasta); se guardan todas. '
  'Para una fila por día usar etl_fob_granos_diario y para el promedio mensual '
  'etl_fob_granos_mensual, que es lo que consume el cálculo de PRP. Tabla append-only: una misma '
  'clave puede tener varios snapshots, el vigente es el de mayor ingested_at (ya resuelto en '
  'etl_fob_granos_actual). Sábados, domingos y feriados no tienen filas: la fuente no cotiza.';

comment on column etl_fob_granos.date is 'Fecha de cotización (día hábil real, no primer día del mes)';
comment on column etl_fob_granos.embarque_desde is 'Primer mes de la ventana de embarque a la que aplica el precio, como primer día del mes';
comment on column etl_fob_granos.embarque_hasta is 'Último mes de la ventana de embarque a la que aplica el precio, como primer día del mes';
comment on column etl_fob_granos.valor is 'Precio FOB oficial en USD por tonelada';
comment on column etl_fob_granos.posicion is 'Posición arancelaria consultada (NCM 8 dígitos + sufijo SIM + dígito verificador)';
comment on column etl_fob_granos.circular is 'Número de circular de MAGyP que fijó el precio. En los 90 una sola circular regía el año entero; hoy cambian casi a diario';

-- Último snapshot por (producto, fecha, ventana de embarque).
create or replace view etl_fob_granos_actual as
select distinct on (producto, date, embarque_desde, embarque_hasta)
       producto, date, embarque_desde, embarque_hasta, valor, posicion, circular, estado,
       fuente, ingested_at
from etl_fob_granos
order by producto, date, embarque_desde, embarque_hasta, ingested_at desc;

-- Una fila por (producto, día): el precio del embarque "spot".
--
-- Criterio: de las ventanas cotizadas ese día se toma la que CUBRE el mes de la cotización y,
-- si ninguna lo cubre (pasa en los últimos días del mes, cuando la fuente ya sólo cotiza meses
-- siguientes), la ventana más cercana hacia adelante. Es el criterio que reproduce la planilla:
-- mayo-2026 da 434,50 / 233,11 / 210,83 / 479,47 USD/ton contra 434 / 233 / 210 / 479 de la
-- planilla, que trunca a entero. Tomar el promedio de TODAS las ventanas del día daría ~11
-- USD/ton de más en soja, porque la curva forward está en contango.
create or replace view etl_fob_granos_diario as
select distinct on (producto, date)
       producto, date, valor, embarque_desde, embarque_hasta, posicion, circular,
       estado, fuente, ingested_at
from etl_fob_granos_actual
order by producto, date,
         (embarque_desde <= date_trunc('month', date)::date
          and embarque_hasta >= date_trunc('month', date)::date) desc,
         embarque_desde;

comment on view etl_fob_granos_diario is
  'Un precio FOB por (producto, día hábil): el de la ventana de embarque que cubre el mes de la '
  'cotización, con fallback a la ventana más próxima hacia adelante cuando ninguna lo cubre. '
  'Descarta el resto de la curva forward que sí vive en etl_fob_granos.';

-- Promedio mensual sobre los días hábiles efectivamente cotizados.
create or replace view etl_fob_granos_mensual as
select producto,
       date_trunc('month', date)::date as date,
       avg(valor)   as valor,
       count(*)     as dias,
       min(date)    as primer_dia,
       max(date)    as ultimo_dia
from etl_fob_granos_diario
group by producto, date_trunc('month', date)::date;

comment on view etl_fob_granos_mensual is
  'Precio FOB mensual en USD/ton: promedio simple de etl_fob_granos_diario sobre los días '
  'hábiles del mes que la fuente efectivamente cotizó (columna dias). Es la entrada del cálculo '
  'de PRP. Un mes en curso trae el promedio parcial de los días transcurridos: filtrar por dias '
  'si se necesita el mes cerrado.';

-- Días hábiles que la fuente declaró SIN cotización (feriados, y días que simplemente no
-- publicó). Existe por el costo de los requests: son ~1.200 días entre 1993 y 2017, y sin este
-- registro `load-history --solo-faltantes` los vuelve a pedir en cada corrida —80 minutos de
-- requests contra un host compartido que nunca van a traer nada—. Un día entra acá sólo cuando
-- la API respondió bien y con la lista vacía, que es como dice "ese día no cotizó"; un error de
-- red o un cuerpo roto NO lo marcan.
create table if not exists etl_fob_granos_sin_dato (
  date     date primary key,
  visto_en timestamptz not null default now()
);

comment on table etl_fob_granos_sin_dato is
  'Días hábiles en los que la API de precios FOB de MAGyP respondió sin datos: feriados y días '
  'que la fuente no publicó. Es un registro de lo ya preguntado, no una serie: sirve para que la '
  'carga histórica no vuelva a gastar un request en un día que ya se sabe vacío. Si hiciera falta '
  'volver a preguntar por un tramo, borrar sus filas o pedirlo con --desde/--hasta, que ignora '
  'este registro.';

-- Seed inferido, no probado uno por uno: los días hábiles que `precios_fob` no tiene dentro de
-- los años donde le faltan <= 25 días. Ese corte separa los feriados del agujero de carga de esa
-- tabla: en 1993-2003, 2005-2007 y 2012-2016 le faltan 8-23 días por año —por debajo de la
-- cantidad de feriados argentinos— mientras que 2004 (48), 2008 (149), 2009 y 2010 (enteros) y
-- 2011 (218) son claramente otra cosa, y ésos NO se siembran: se preguntan por API.
-- La inferencia se validó con una muestra al azar de 10 de estos días, pedidos a la API el
-- 2026-09-11: los 10 volvieron vacíos y los 10 son feriados argentinos reconocibles
-- (Jueves Santo, 1 de mayo, Día de la Memoria, puentes de San Martín y del 9 de julio).
-- Si alguno resultara no serlo, borrar su fila y volver a pedirlo.
insert into etl_fob_granos_sin_dato (date)
select h.d
from generate_series(date '1993-01-04', date '2017-04-19', interval '1 day') g(d0)
cross join lateral (select g.d0::date as d) h
join (select a from (
        select extract(year from x.d)::int a, count(*) filter (where t.d is null) faltan
        from (select gg::date d from generate_series(date '1993-01-04', date '2017-04-19',
                                                     interval '1 day') gg
              where extract(isodow from gg) < 6) x
        left join (select distinct date d from precios_fob) t using (d)
        group by 1) y where faltan <= 25) anios on anios.a = extract(year from h.d)::int
left join (select distinct date d from precios_fob) t on t.d = h.d
where extract(isodow from h.d) < 6 and t.d is null
on conflict (date) do nothing;

-- ---------------------------------------------------------------------------------------------
-- Referencia: derechos de exportación (DEX)
-- ---------------------------------------------------------------------------------------------

create table if not exists dex (
  producto text not null check (producto in ('soja','trigo','maiz','girasol')),
  desde    date not null,
  hasta    date not null default date '2999-12-31',
  dex      double precision not null check (dex >= 0 and dex <= 1),
  nota     text,
  constraint dex_rango_valido check (hasta >= desde),
  constraint dex_pk primary key (producto, desde),
  -- Sin esto, un tramo mal cargado por el CRUD haría que un mes matchee dos filas y el PRP se
  -- duplique en silencio. Con el EXCLUDE el error salta al escribir, no al leer.
  constraint dex_sin_solapamiento exclude using gist (
    producto with =, daterange(desde, hasta, '[]') with &&)
);

comment on table dex is
  'Derechos de exportación (retenciones) por grano, vigentes por tramo de fechas. Una fila por '
  'cada cambio de alícuota: los tramos de un mismo producto no se solapan y el tramo vigente '
  'lleva hasta = 2999-12-31. Para consultar la alícuota de una fecha: '
  'select dex from dex where producto = ''soja'' and ''2024-05-01''::date between desde and hasta. '
  'Para extender la serie hay que CERRAR el tramo vigente (hasta = día anterior al cambio) e '
  'insertar el nuevo con hasta = 2999-12-31; la restricción de no-solapamiento lo fuerza. '
  'El valor se guarda en TANTO POR UNO (0.26 = 26 %), no en porcentaje, porque entra al cálculo '
  'como (1 - dex). Seed cargado desde la planilla TCR Granos.xlsx (columna I de cada solapa), '
  'tramos 1993-01 en adelante; de ahí en más lo mantiene un CRUD aparte. Los valores de '
  '2018-09 a 2019-11 no son alícuotas redondas: ese período rigió el derecho fijo en pesos por '
  'dólar exportado (Decreto 793/2018) y la planilla lo guarda ya convertido a equivalente '
  'ad valorem, que se mueve mes a mes con el tipo de cambio y el precio FOB.';

comment on column dex.desde is 'Primer día de vigencia del tramo (inclusive)';
comment on column dex.hasta is 'Último día de vigencia del tramo (inclusive). 2999-12-31 marca el tramo vigente';
comment on column dex.dex is 'Alícuota en tanto por uno: 0.26 = 26 %';
comment on column dex.nota is 'Norma o comentario del tramo, opcional';

insert into dex (producto, desde, hasta, dex) values
  ('soja', date '1993-01-01', date '2002-02-28', 0.035),
  ('soja', date '2002-03-01', date '2002-03-31', 0.135),
  ('soja', date '2002-04-01', date '2006-12-31', 0.235),
  ('soja', date '2007-01-01', date '2007-10-31', 0.275),
  ('soja', date '2007-11-01', date '2015-11-30', 0.35),
  ('soja', date '2015-12-01', date '2017-12-31', 0.3),
  ('soja', date '2018-01-01', date '2018-01-31', 0.295),
  ('soja', date '2018-02-01', date '2018-02-28', 0.29),
  ('soja', date '2018-03-01', date '2018-03-31', 0.285),
  ('soja', date '2018-04-01', date '2018-04-30', 0.28),
  ('soja', date '2018-05-01', date '2018-05-31', 0.275),
  ('soja', date '2018-06-01', date '2018-06-30', 0.27),
  ('soja', date '2018-07-01', date '2018-07-31', 0.265),
  ('soja', date '2018-08-01', date '2018-08-31', 0.26),
  ('soja', date '2018-09-01', date '2018-09-30', 0.28365379632),
  ('soja', date '2018-10-01', date '2018-10-31', 0.287758040097),
  ('soja', date '2018-11-01', date '2018-11-30', 0.289712279547),
  ('soja', date '2018-12-01', date '2018-12-31', 0.285582127058),
  ('soja', date '2019-01-01', date '2019-01-31', 0.286932143535),
  ('soja', date '2019-02-01', date '2019-02-28', 0.284143342897),
  ('soja', date '2019-03-01', date '2019-03-31', 0.276706187262),
  ('soja', date '2019-04-01', date '2019-04-30', 0.2725202041),
  ('soja', date '2019-05-01', date '2019-05-31', 0.269021035671),
  ('soja', date '2019-06-01', date '2019-06-30', 0.271346307554),
  ('soja', date '2019-07-01', date '2019-07-31', 0.274021634378),
  ('soja', date '2019-08-01', date '2019-08-31', 0.25586231748),
  ('soja', date '2019-09-01', date '2019-09-30', 0.250794705972),
  ('soja', date '2019-10-01', date '2019-10-31', 0.248340087612),
  ('soja', date '2019-11-01', date '2019-11-30', 0.24695894245),
  ('soja', date '2019-12-01', date '2020-02-29', 0.3),
  ('soja', date '2020-03-01', date '2025-01-31', 0.33),
  ('soja', date '2025-02-01', date '2025-06-30', 0.26),
  ('soja', date '2025-07-01', date '2025-07-31', 0.33),
  ('soja', date '2025-08-01', date '2025-08-31', 0.26),
  ('soja', date '2025-09-01', date '2025-09-30', 0),
  ('soja', date '2025-10-01', date '2025-11-30', 0.26),
  ('soja', date '2025-12-01', date '2999-12-31', 0.24),
  ('trigo', date '1993-01-01', date '2002-03-31', 0),
  ('trigo', date '2002-04-01', date '2007-10-31', 0.2),
  ('trigo', date '2007-11-01', date '2015-11-30', 0.28),
  ('trigo', date '2015-12-01', date '2015-12-31', 0.14),
  ('trigo', date '2016-01-01', date '2018-08-31', 0),
  ('trigo', date '2018-09-01', date '2018-09-30', 0.10365379632),
  ('trigo', date '2018-10-01', date '2018-10-31', 0.107758040097),
  ('trigo', date '2018-11-01', date '2018-11-30', 0.109712279547),
  ('trigo', date '2018-12-01', date '2018-12-31', 0.105582127058),
  ('trigo', date '2019-01-01', date '2019-01-31', 0.106932143535),
  ('trigo', date '2019-02-01', date '2019-02-28', 0.104143342897),
  ('trigo', date '2019-03-01', date '2019-03-31', 0.096706187262),
  ('trigo', date '2019-04-01', date '2019-04-30', 0.0925202041),
  ('trigo', date '2019-05-01', date '2019-05-31', 0.089021035671),
  ('trigo', date '2019-06-01', date '2019-06-30', 0.091346307554),
  ('trigo', date '2019-07-01', date '2019-07-31', 0.094021634378),
  ('trigo', date '2019-08-01', date '2019-08-31', 0.07586231748),
  ('trigo', date '2019-09-01', date '2019-09-30', 0.070794705972),
  ('trigo', date '2019-10-01', date '2019-10-31', 0.068340087612),
  ('trigo', date '2019-11-01', date '2019-11-30', 0.06695894245),
  ('trigo', date '2019-12-01', date '2025-01-31', 0.12),
  ('trigo', date '2025-02-01', date '2025-06-30', 0.095),
  ('trigo', date '2025-07-01', date '2025-07-31', 0.12),
  ('trigo', date '2025-08-01', date '2025-08-31', 0.095),
  ('trigo', date '2025-09-01', date '2025-09-30', 0),
  ('trigo', date '2025-10-01', date '2025-11-30', 0.095),
  ('trigo', date '2025-12-01', date '2025-12-31', 0.075),
  ('trigo', date '2026-01-01', date '2026-05-31', 0.085),
  ('trigo', date '2026-06-01', date '2999-12-31', 0.055),
  ('maiz', date '1993-01-01', date '2002-03-31', 0),
  ('maiz', date '2002-04-01', date '2007-10-31', 0.2),
  ('maiz', date '2007-11-01', date '2008-11-30', 0.25),
  ('maiz', date '2008-12-01', date '2015-11-30', 0.2),
  ('maiz', date '2015-12-01', date '2015-12-31', 0.1),
  ('maiz', date '2016-01-01', date '2018-08-31', 0),
  ('maiz', date '2018-09-01', date '2018-09-30', 0.10365379632),
  ('maiz', date '2018-10-01', date '2018-10-31', 0.107758040097),
  ('maiz', date '2018-11-01', date '2018-11-30', 0.109712279547),
  ('maiz', date '2018-12-01', date '2018-12-31', 0.105582127058),
  ('maiz', date '2019-01-01', date '2019-01-31', 0.106932143535),
  ('maiz', date '2019-02-01', date '2019-02-28', 0.104143342897),
  ('maiz', date '2019-03-01', date '2019-03-31', 0.096706187262),
  ('maiz', date '2019-04-01', date '2019-04-30', 0.0925202041),
  ('maiz', date '2019-05-01', date '2019-05-31', 0.089021035671),
  ('maiz', date '2019-06-01', date '2019-06-30', 0.091346307554),
  ('maiz', date '2019-07-01', date '2019-07-31', 0.094021634378),
  ('maiz', date '2019-08-01', date '2019-08-31', 0.07586231748),
  ('maiz', date '2019-09-01', date '2019-09-30', 0.070794705972),
  ('maiz', date '2019-10-01', date '2019-10-31', 0.068340087612),
  ('maiz', date '2019-11-01', date '2019-11-30', 0.06695894245),
  ('maiz', date '2019-12-01', date '2025-01-31', 0.12),
  ('maiz', date '2025-02-01', date '2025-06-30', 0.095),
  ('maiz', date '2025-07-01', date '2025-07-31', 0.12),
  ('maiz', date '2025-08-01', date '2025-08-31', 0.095),
  ('maiz', date '2025-09-01', date '2025-09-30', 0),
  ('maiz', date '2025-10-01', date '2025-11-30', 0.095),
  ('maiz', date '2025-12-01', date '2999-12-31', 0.085),
  ('girasol', date '1993-01-01', date '2002-01-31', 0.035),
  ('girasol', date '2002-02-01', date '2002-03-31', 0.135),
  ('girasol', date '2002-04-01', date '2007-10-31', 0.235),
  ('girasol', date '2007-11-01', date '2015-11-30', 0.32),
  ('girasol', date '2015-12-01', date '2015-12-31', 0.16),
  ('girasol', date '2016-01-01', date '2018-08-31', 0),
  ('girasol', date '2018-09-01', date '2018-09-30', 0.10365379632),
  ('girasol', date '2018-10-01', date '2018-10-31', 0.107758040097),
  ('girasol', date '2018-11-01', date '2018-11-30', 0.109712279547),
  ('girasol', date '2018-12-01', date '2018-12-31', 0.105582127058),
  ('girasol', date '2019-01-01', date '2019-01-31', 0.106932143535),
  ('girasol', date '2019-02-01', date '2019-02-28', 0.104143342897),
  ('girasol', date '2019-03-01', date '2019-03-31', 0.096706187262),
  ('girasol', date '2019-04-01', date '2019-04-30', 0.0925202041),
  ('girasol', date '2019-05-01', date '2019-05-31', 0.089021035671),
  ('girasol', date '2019-06-01', date '2019-06-30', 0.091346307554),
  ('girasol', date '2019-07-01', date '2019-07-31', 0.094021634378),
  ('girasol', date '2019-08-01', date '2019-08-31', 0.07586231748),
  ('girasol', date '2019-09-01', date '2019-09-30', 0.070794705972),
  ('girasol', date '2019-10-01', date '2019-10-31', 0.068340087612),
  ('girasol', date '2019-11-01', date '2019-11-30', 0.06695894245),
  ('girasol', date '2019-12-01', date '2020-03-31', 0.12),
  ('girasol', date '2020-04-01', date '2025-01-31', 0.07),
  ('girasol', date '2025-02-01', date '2025-06-30', 0.055),
  ('girasol', date '2025-07-01', date '2025-07-31', 0.075),
  ('girasol', date '2025-08-01', date '2025-08-31', 0.055),
  ('girasol', date '2025-09-01', date '2025-09-30', 0),
  ('girasol', date '2025-10-01', date '2025-11-30', 0.055),
  ('girasol', date '2025-12-01', date '2999-12-31', 0.045)
on conflict (producto, desde) do update set
  hasta = excluded.hasta, dex = excluded.dex;

-- ---------------------------------------------------------------------------------------------
-- Referencia: pesos VBP de la canasta de granos
-- ---------------------------------------------------------------------------------------------

create table if not exists vbp_granos (
  producto text not null check (producto in ('soja','trigo','maiz','girasol')),
  desde    date not null,
  hasta    date not null default date '2999-12-31',
  vbp      double precision not null check (vbp >= 0 and vbp <= 1),
  nota     text,
  constraint vbp_granos_rango_valido check (hasta >= desde),
  constraint vbp_granos_pk primary key (producto, desde),
  constraint vbp_granos_sin_solapamiento exclude using gist (
    producto with =, daterange(desde, hasta, '[]') with &&)
);

comment on table vbp_granos is
  'Ponderadores de la canasta de cuatro granos, por valor bruto de producción (VBP), vigentes '
  'por tramo de fechas. Misma forma que dex: tramos sin solapamiento por producto y el vigente '
  'con hasta = 2999-12-31. Se guardan en TANTO POR UNO (0.6217 = 62,17 %) y los cuatro de un '
  'mismo tramo suman 1. Se usan para combinar los PRP de cada grano en un PRP único: '
  'prp_combinado = SUM(prp_grano * vbp_grano). Seed: pesos VBP 2026 de la planilla '
  'TCR Granos.xlsx (solapa "4 GRANOS", fila 2); el de girasol es el residuo 1 - soja - trigo - maíz.';

comment on column vbp_granos.vbp is 'Ponderador en tanto por uno: 0.6217 = 62,17 %. Los cuatro granos de un tramo suman 1';

insert into vbp_granos (producto, desde, hasta, vbp, nota) values
  ('soja',    date '1993-01-01', date '2999-12-31', 0.6217093600968505, 'pesos VBP 2026 (planilla TCR Granos)'),
  ('trigo',   date '1993-01-01', date '2999-12-31', 0.1477526520356099, 'pesos VBP 2026 (planilla TCR Granos)'),
  ('maiz',    date '1993-01-01', date '2999-12-31', 0.1742153356522137, 'pesos VBP 2026 (planilla TCR Granos)'),
  ('girasol', date '1993-01-01', date '2999-12-31', 0.0563226522153259, 'pesos VBP 2026 (planilla TCR Granos), residuo 1 - soja - trigo - maiz')
on conflict (producto, desde) do update set
  hasta = excluded.hasta, vbp = excluded.vbp, nota = excluded.nota;

-- ---------------------------------------------------------------------------------------------
-- Referencia: tipo de cambio del exportador (sólo excepciones al A3500)
-- ---------------------------------------------------------------------------------------------

create table if not exists tc_granos (
  desde  date not null,
  hasta  date not null default date '2999-12-31',
  tc     double precision not null check (tc > 0),
  motivo text not null,
  nota   text,
  constraint tc_granos_rango_valido check (hasta >= desde),
  constraint tc_granos_pk primary key (desde),
  constraint tc_granos_sin_solapamiento exclude using gist (
    daterange(desde, hasta, '[]') with &&)
);

comment on table tc_granos is
  'Tipo de cambio mensual que cobra el exportador de granos, SÓLO para los meses en que no es el '
  'A3500 del BCRA. Es una tabla de excepciones: la vista tc_granos_mensual usa el promedio '
  'mensual del A3500 y sólo pisa los meses que aparecen acá. Dos motivos la pueblan. '
  '(1) motivo = ''convertibilidad'': 1993-01 a 2002-02, meses anteriores al primer dato del '
  'A3500 (2002-03-04); los valores salen de la planilla TCR Granos.xlsx. '
  '(2) motivo = ''dolar_exportador'': 2022-09 a 2024-12, los programas de incremento exportador '
  '(dólar soja/agro a 200, 300 y 340 $/USD) y el blend 80/20 oficial-CCL que rigió hasta '
  'diciembre de 2024; también salen de la planilla. Desde 2025-01 el A3500 vuelve a ser el tipo '
  'de cambio efectivo y la tabla no tiene filas. Los valores son promedios MENSUALES en pesos '
  'por dólar y cada fila cubre un mes calendario completo.';

comment on column tc_granos.motivo is 'Por qué este mes no usa el A3500: convertibilidad (previo a la serie del BCRA) o dolar_exportador (régimen diferencial)';

insert into tc_granos (desde, hasta, tc, motivo) values
(date '1993-01-01', date '1993-01-31', 0.99874, 'convertibilidad'),
  (date '1993-02-01', date '1993-02-28', 0.999155, 'convertibilidad'),
  (date '1993-03-01', date '1993-03-31', 0.999687, 'convertibilidad'),
  (date '1993-04-01', date '1993-04-30', 0.99916, 'convertibilidad'),
  (date '1993-05-01', date '1993-05-31', 0.999395, 'convertibilidad'),
  (date '1993-06-01', date '1993-06-30', 0.9992857, 'convertibilidad'),
  (date '1993-07-01', date '1993-07-31', 0.9989238, 'convertibilidad'),
  (date '1993-08-01', date '1993-08-31', 1.000175, 'convertibilidad'),
  (date '1993-09-01', date '1993-09-30', 1.0010095, 'convertibilidad'),
  (date '1993-10-01', date '1993-10-31', 0.999515, 'convertibilidad'),
  (date '1993-11-01', date '1993-11-30', 0.998519, 'convertibilidad'),
  (date '1993-12-01', date '1993-12-31', 0.9985952, 'convertibilidad'),
  (date '1994-01-01', date '1994-01-31', 0.9985714, 'convertibilidad'),
  (date '1994-02-01', date '1994-02-28', 0.998995, 'convertibilidad'),
  (date '1994-03-01', date '1994-03-31', 1.0005273, 'convertibilidad'),
  (date '1994-04-01', date '1994-04-30', 1.000425, 'convertibilidad'),
  (date '1994-05-01', date '1994-05-31', 0.998919, 'convertibilidad'),
  (date '1994-06-01', date '1994-06-30', 0.997895, 'convertibilidad'),
  (date '1994-07-01', date '1994-07-31', 0.998262, 'convertibilidad'),
  (date '1994-08-01', date '1994-08-31', 0.9956619, 'convertibilidad'),
  (date '1994-09-01', date '1994-09-30', 0.9988524, 'convertibilidad'),
  (date '1994-10-01', date '1994-10-31', 0.99869, 'convertibilidad'),
  (date '1994-11-01', date '1994-11-30', 1.000833, 'convertibilidad'),
  (date '1994-12-01', date '1994-12-31', 1.00185, 'convertibilidad'),
  (date '1995-01-01', date '1995-01-31', 1.0019181818, 'convertibilidad'),
  (date '1995-02-01', date '1995-02-28', 1.001935, 'convertibilidad'),
  (date '1995-03-01', date '1995-03-31', 1.00375, 'convertibilidad'),
  (date '1995-04-01', date '1995-04-30', 1.0014944444, 'convertibilidad'),
  (date '1995-05-01', date '1995-05-31', 1.0003952381, 'convertibilidad'),
  (date '1995-06-01', date '1995-06-30', 0.9993380952, 'convertibilidad'),
  (date '1995-07-01', date '1995-07-31', 0.99941, 'convertibilidad'),
  (date '1995-08-01', date '1995-08-31', 0.99987, 'convertibilidad'),
  (date '1995-09-01', date '1995-09-30', 0.9990428571, 'convertibilidad'),
  (date '1995-10-01', date '1995-10-31', 0.99944, 'convertibilidad'),
  (date '1995-11-01', date '1995-11-30', 0.9996952381, 'convertibilidad'),
  (date '1995-12-01', date '1995-12-31', 1.0001166667, 'convertibilidad'),
  (date '1996-01-01', date '1996-01-31', 0.9992181818, 'convertibilidad'),
  (date '1996-02-01', date '1996-02-29', 0.9993952381, 'convertibilidad'),
  (date '1996-03-01', date '1996-03-31', 0.9999714286, 'convertibilidad'),
  (date '1996-04-01', date '1996-04-30', 0.9993, 'convertibilidad'),
  (date '1996-05-01', date '1996-05-31', 1.0001, 'convertibilidad'),
  (date '1996-06-01', date '1996-06-30', 1.0007, 'convertibilidad'),
  (date '1996-07-01', date '1996-07-31', 1.0014357143, 'convertibilidad'),
  (date '1996-08-01', date '1996-08-31', 1.0011952381, 'convertibilidad'),
  (date '1996-09-01', date '1996-09-30', 1.0002714286, 'convertibilidad'),
  (date '1996-10-01', date '1996-10-31', 0.999673913, 'convertibilidad'),
  (date '1996-11-01', date '1996-11-30', 0.9993095238, 'convertibilidad'),
  (date '1996-12-01', date '1996-12-31', 1.0002, 'convertibilidad'),
  (date '1997-01-01', date '1997-01-31', 0.999, 'convertibilidad'),
  (date '1997-02-01', date '1997-02-28', 0.9992, 'convertibilidad'),
  (date '1997-03-01', date '1997-03-31', 0.9993, 'convertibilidad'),
  (date '1997-04-01', date '1997-04-30', 0.9992, 'convertibilidad'),
  (date '1997-05-01', date '1997-05-31', 0.9993, 'convertibilidad'),
  (date '1997-06-01', date '1997-06-30', 0.9994, 'convertibilidad'),
  (date '1997-07-01', date '1997-07-31', 0.9998, 'convertibilidad'),
  (date '1997-08-01', date '1997-08-31', 0.9997, 'convertibilidad'),
  (date '1997-09-01', date '1997-09-30', 0.9997, 'convertibilidad'),
  (date '1997-10-01', date '1997-10-31', 1.0004, 'convertibilidad'),
  (date '1997-11-01', date '1997-11-30', 1.0011, 'convertibilidad'),
  (date '1997-12-01', date '1997-12-31', 1.0007, 'convertibilidad'),
  (date '1998-01-01', date '1998-01-31', 0.9994, 'convertibilidad'),
  (date '1998-02-01', date '1998-02-28', 0.9991, 'convertibilidad'),
  (date '1998-03-01', date '1998-03-31', 0.9999, 'convertibilidad'),
  (date '1998-04-01', date '1998-04-30', 1.0007, 'convertibilidad'),
  (date '1998-05-01', date '1998-05-31', 0.9999, 'convertibilidad'),
  (date '1998-06-01', date '1998-06-30', 0.9999, 'convertibilidad'),
  (date '1998-07-01', date '1998-07-31', 0.9999, 'convertibilidad'),
  (date '1998-08-01', date '1998-08-31', 0.9999, 'convertibilidad'),
  (date '1998-09-01', date '1998-09-30', 0.9999, 'convertibilidad'),
  (date '1998-10-01', date '1998-10-31', 0.9999, 'convertibilidad'),
  (date '1998-11-01', date '1998-11-30', 1.0002, 'convertibilidad'),
  (date '1998-12-01', date '1998-12-31', 0.9998, 'convertibilidad'),
  (date '1999-01-01', date '1999-01-31', 0.999945, 'convertibilidad'),
  (date '1999-02-01', date '1999-02-28', 1.00024, 'convertibilidad'),
  (date '1999-03-01', date '1999-03-31', 0.9996869565, 'convertibilidad'),
  (date '1999-04-01', date '1999-04-30', 0.999415, 'convertibilidad'),
  (date '1999-05-01', date '1999-05-31', 1.00017, 'convertibilidad'),
  (date '1999-06-01', date '1999-06-30', 1.000115, 'convertibilidad'),
  (date '1999-07-01', date '1999-07-31', 1.0003142857, 'convertibilidad'),
  (date '1999-08-01', date '1999-08-31', 1.0001666667, 'convertibilidad'),
  (date '1999-09-01', date '1999-09-30', 1.0004045455, 'convertibilidad'),
  (date '1999-10-01', date '1999-10-31', 1.00134, 'convertibilidad'),
  (date '1999-11-01', date '1999-11-30', 1.0006636364, 'convertibilidad'),
  (date '1999-12-01', date '1999-12-31', 1.0015428571, 'convertibilidad'),
  (date '2000-01-01', date '2000-01-31', 0.9996666667, 'convertibilidad'),
  (date '2000-02-01', date '2000-02-29', 0.9990809524, 'convertibilidad'),
  (date '2000-03-01', date '2000-03-31', 0.9990826087, 'convertibilidad'),
  (date '2000-04-01', date '2000-04-30', 0.9990388889, 'convertibilidad'),
  (date '2000-05-01', date '2000-05-31', 0.9998285714, 'convertibilidad'),
  (date '2000-06-01', date '2000-06-30', 0.9996, 'convertibilidad'),
  (date '2000-07-01', date '2000-07-31', 0.9984571429, 'convertibilidad'),
  (date '2000-08-01', date '2000-08-31', 0.9990409091, 'convertibilidad'),
  (date '2000-09-01', date '2000-09-30', 0.9988333333, 'convertibilidad'),
  (date '2000-10-01', date '2000-10-31', 0.9991285714, 'convertibilidad'),
  (date '2000-11-01', date '2000-11-30', 0.9998227273, 'convertibilidad'),
  (date '2000-12-01', date '2000-12-31', 1.0004277778, 'convertibilidad'),
  (date '2001-01-01', date '2001-01-31', 0.9996681818, 'convertibilidad'),
  (date '2001-02-01', date '2001-02-28', 0.99899, 'convertibilidad'),
  (date '2001-03-01', date '2001-03-31', 1.0035, 'convertibilidad'),
  (date '2001-04-01', date '2001-04-30', 1.0000222222, 'convertibilidad'),
  (date '2001-05-01', date '2001-05-31', 0.9994952381, 'convertibilidad'),
  (date '2001-06-01', date '2001-06-30', 0.99989, 'convertibilidad'),
  (date '2001-07-01', date '2001-07-31', 1.0063952381, 'convertibilidad'),
  (date '2001-08-01', date '2001-08-31', 1.0005391304, 'convertibilidad'),
  (date '2001-09-01', date '2001-09-30', 0.999805, 'convertibilidad'),
  (date '2001-10-01', date '2001-10-31', 1.0024130435, 'convertibilidad'),
  (date '2001-11-01', date '2001-11-30', 1.0039409091, 'convertibilidad'),
  (date '2001-12-01', date '2001-12-31', 1.0546000375, 'convertibilidad'),
  (date '2002-01-01', date '2002-01-31', 1.6072727273, 'convertibilidad'),
  (date '2002-02-01', date '2002-02-28', 2.128, 'convertibilidad'),
  (date '2022-09-01', date '2022-09-30', 200, 'dolar_exportador'),
  (date '2022-12-01', date '2022-12-31', 230, 'dolar_exportador'),
  (date '2023-04-01', date '2023-04-30', 300, 'dolar_exportador'),
  (date '2023-05-01', date '2023-05-31', 300, 'dolar_exportador'),
  (date '2023-06-01', date '2023-06-30', 266.4647, 'dolar_exportador'),
  (date '2023-07-01', date '2023-07-31', 340, 'dolar_exportador'),
  (date '2023-08-01', date '2023-08-31', 340, 'dolar_exportador'),
  (date '2023-09-01', date '2023-09-30', 451.97881125, 'dolar_exportador'),
  (date '2023-10-01', date '2023-10-31', 496.5401402273, 'dolar_exportador'),
  (date '2023-11-01', date '2023-11-30', 551.279217619, 'dolar_exportador'),
  (date '2023-12-01', date '2023-12-31', 769.2791015789, 'dolar_exportador'),
  (date '2024-01-01', date '2024-01-31', 896.1345454545, 'dolar_exportador'),
  (date '2024-02-01', date '2024-02-29', 901.8929136842, 'dolar_exportador'),
  (date '2024-03-01', date '2024-03-31', 898.0886884848, 'dolar_exportador'),
  (date '2024-04-01', date '2024-04-30', 906.3250944, 'dolar_exportador'),
  (date '2024-05-01', date '2024-05-31', 939.5974782609, 'dolar_exportador'),
  (date '2024-06-01', date '2024-06-30', 983.640504, 'dolar_exportador'),
  (date '2024-07-01', date '2024-07-31', 1009.02676, 'dolar_exportador'),
  (date '2024-08-01', date '2024-08-31', 1013.6240836364, 'dolar_exportador'),
  (date '2024-09-01', date '2024-09-30', 1019.0886095238, 'dolar_exportador'),
  (date '2024-10-01', date '2024-10-31', 1023.4686330435, 'dolar_exportador'),
  (date '2024-11-01', date '2024-11-30', 1028.994164, 'dolar_exportador'),
  (date '2024-12-01', date '2024-12-31', 1042.001268, 'dolar_exportador')
on conflict (desde) do update set
  hasta = excluded.hasta, tc = excluded.tc, motivo = excluded.motivo;

-- Tipo de cambio mensual efectivo del exportador: A3500 salvo excepción en tc_granos.
create or replace view tc_granos_mensual as
select g.fecha::date                                          as fecha,
       coalesce(o.tc, a.tc)                                   as tc,
       coalesce(o.motivo, 'a3500')                            as motivo
from generate_series(date '1993-01-01',
                     greatest(date_trunc('month', current_date)::date,
                              coalesce((select max(date) from etl_fob_granos), date '1993-01-01')),
                     interval '1 month') g(fecha)
left join tc_granos o
       on g.fecha::date between o.desde and o.hasta
left join (select date_trunc('month', date)::date as fecha, avg("A3500") as tc
           from "A3500" group by 1) a
       on a.fecha = g.fecha::date;

comment on view tc_granos_mensual is
  'Tipo de cambio mensual en pesos por dólar que se usa para pasar el FOB a pesos: promedio '
  'mensual del A3500 del BCRA, pisado por tc_granos en los meses de convertibilidad (previos a '
  '2002-03) y de dólar exportador (2022-09 a 2024-12). La columna motivo dice cuál se aplicó.';

-- IPC con base 1993 = 1 (el índice que usa la planilla).
create or replace view ipc_base_1993 as
select fecha,
       indice / (select avg(indice) from deflactores
                 where deflactor = 'ipc_largo'
                   and fecha between date '1993-01-01' and date '1993-12-01') as indice,
       origen
from deflactores
where deflactor = 'ipc_largo';

comment on view ipc_base_1993 is
  'IPC largo (public.deflactores, deflactor = ''ipc_largo'') reescalado a base 1993 = 1, es decir '
  'dividido por el promedio de los doce meses de 1993. Es el deflactor que usa el PRP de granos. '
  'Se eligió deflactores y no indices_inflacion porque esta última arranca en 2016-12 y el PRP '
  'empieza en 1993-01. origen = ''proyectado'' marca los meses cuyo IPC todavía no publicó INDEC '
  'y se estimó: esos PRP se revisan.';

-- ---------------------------------------------------------------------------------------------
-- Referencia: overrides manuales del FOB mensual
-- ---------------------------------------------------------------------------------------------

create table if not exists fob_granos_override (
  producto text not null check (producto in ('soja','trigo','maiz','girasol')),
  date     date not null,
  valor    double precision not null check (valor > 0),
  motivo   text not null,
  nota     text,
  constraint fob_granos_override_pk primary key (producto, date),
  constraint fob_granos_override_es_mes check (date = date_trunc('month', date)::date)
);

comment on table fob_granos_override is
  'Pisa el FOB mensual de un (producto, mes) con un valor puesto a mano. Es la válvula de escape '
  'para los meses en que el promedio calculado no es el que se quiere usar; la consume '
  'fob_granos_mensual y, por ahí, el PRP. NO toca etl_fob_granos ni etl_fob_granos_mensual, que '
  'siguen mostrando lo que bajó el ETL: la vista expone las dos cifras (valor y valor_etl) y una '
  'columna origen que dice cuál se aplicó. Una fila por mes, con date en el primer día del mes. '
  'Sacar un override es borrar su fila. Cada fila DEBE explicar en motivo por qué está.';

comment on column fob_granos_override.date is 'Mes pisado, como primer día del mes';
comment on column fob_granos_override.valor is 'Precio FOB en USD por tonelada que reemplaza al promedio calculado';
comment on column fob_granos_override.motivo is 'Por qué se pisa este mes. Obligatorio: un override sin explicación es un dato perdido';

-- FOB mensual efectivo: el del ETL, salvo que fob_granos_override lo pise.
create or replace view fob_granos_mensual as
select coalesce(m.producto, o.producto)                              as producto,
       coalesce(m.date, o.date)                                      as date,
       coalesce(o.valor, m.valor)                                    as valor,
       case when o.valor is null then 'etl' else 'override' end      as origen,
       m.valor                                                       as valor_etl,
       o.motivo                                                      as override_motivo,
       m.dias, m.primer_dia, m.ultimo_dia
from etl_fob_granos_mensual m
full join fob_granos_override o on o.producto = m.producto and o.date = m.date;

comment on view fob_granos_mensual is
  'Precio FOB mensual en USD/ton que consume el PRP: el promedio de etl_fob_granos_mensual, '
  'pisado por fob_granos_override donde haya una fila. La columna origen dice cuál se aplicó '
  '(etl / override) y valor_etl conserva siempre el promedio calculado, así el override nunca '
  'esconde el dato original. Sin prefijo etl_ a propósito: mezcla lo que bajó el ETL con lo que '
  'se mantiene a mano, igual que dex, vbp_granos y tc_granos. Hoy fob_granos_override está VACÍA: '
  'la vista devuelve el promedio calculado para todos los meses.';

-- PRP por grano: el cálculo de la planilla, columna por columna.
--
-- Esta vista es la DEFINICION; lo que se consume es la materializada `granos_prp`, que se
-- refresca al final de cada corrida de `python -m etl fob_granos`. El motivo es medido, no
-- estetico: la cadena completa tarda ~1,7 s por consulta y el 43% de eso es `deflactores`, que
-- no es de este repo, cuesta ~400 ms por escaneo y se escanea DOS veces (la serie y el promedio
-- de 1993 del subquery de ipc_base_1993). Materializada, la misma consulta baja a ~10 ms.
drop view if exists granos_prp_combinado;
drop view if exists granos_prp;
drop materialized view if exists granos_prp_combinado;
drop materialized view if exists granos_prp;

create or replace view granos_prp_calc as
select f.producto,
       f.date                                       as fecha,
       f.valor                                      as fob_usd,
       f.dias                                       as dias_cotizados,
       t.tc                                         as tc,
       t.motivo                                     as tc_motivo,
       i.indice                                     as ipc_1993,
       i.origen                                     as deflactor_origen,
       f.valor * t.tc / i.indice                    as fob_real,
       d.dex                                        as dex,
       f.valor * t.tc / i.indice * (1 - d.dex)      as prp,
       f.origen                                     as fob_origen
from fob_granos_mensual f
join tc_granos_mensual t on t.fecha = f.date
join ipc_base_1993     i on i.fecha = f.date
join dex               d on d.producto = f.producto and f.date between d.desde and d.hasta;

create materialized view if not exists granos_prp as
  select * from granos_prp_calc;

-- Unico por (producto, fecha): lo exige REFRESH ... CONCURRENTLY, que es lo que permite
-- refrescar sin bloquear a quien este leyendo.
create unique index if not exists granos_prp_uq on granos_prp (producto, fecha);
create index if not exists granos_prp_fecha_idx on granos_prp (fecha);

comment on materialized view granos_prp is
  'PRP (precio relativo de los productos) mensual por grano, en pesos de 1993 por tonelada. '
  'Reproduce la planilla TCR Granos.xlsx, una solapa por grano: '
  'fob_real = fob_usd * tc / ipc_1993 y prp = fob_real * (1 - dex). '
  'Entradas: etl_fob_granos_mensual (FOB oficial MAGyP promediado por mes), tc_granos_mensual '
  '(A3500 con excepciones), ipc_base_1993 (deflactores/ipc_largo rebaseado) y dex (retenciones). '
  'Un mes sólo aparece si tiene las cuatro entradas. Filtrar por deflactor_origen = ''publicado'' '
  'para excluir los meses con IPC estimado, y mirar dias_cotizados para saber si el mes está '
  'completo.';

-- PRP combinado: los cuatro PRP ponderados por VBP, con los componentes a la vista.
create or replace view granos_prp_combinado_calc as
select p.fecha,
       max(p.prp) filter (where p.producto = 'soja')    as prp_soja,
       max(p.prp) filter (where p.producto = 'trigo')   as prp_trigo,
       max(p.prp) filter (where p.producto = 'maiz')    as prp_maiz,
       max(p.prp) filter (where p.producto = 'girasol') as prp_girasol,
       sum(p.prp * v.vbp)                               as prp_combinado,
       min(p.dias_cotizados)                            as dias_cotizados,
       max(p.deflactor_origen)                          as deflactor_origen,
       count(*) filter (where p.fob_origen = 'override') as fob_overrides
from granos_prp p
join vbp_granos v on v.producto = p.producto and p.fecha between v.desde and v.hasta
group by p.fecha
having count(*) = 4;

create materialized view if not exists granos_prp_combinado as
  select * from granos_prp_combinado_calc;

create unique index if not exists granos_prp_combinado_uq on granos_prp_combinado (fecha);

comment on materialized view granos_prp_combinado is
  'PRP combinado mensual de la canasta de granos, en pesos de 1993 por tonelada: '
  'prp_soja * vbp_soja + prp_trigo * vbp_trigo + prp_maiz * vbp_maiz + prp_girasol * vbp_girasol, '
  'con los ponderadores de vbp_granos. Equivale a la columna K de la solapa "4 GRANOS" de la '
  'planilla TCR Granos.xlsx, y las cuatro columnas prp_* a las columnas C a F de esa misma solapa. '
  'Un mes aparece SÓLO si los cuatro granos tienen PRP: un mes incompleto quedaría subponderado, '
  'que es justo el error que comete la planilla en los meses de cola (devuelve 0). '
  'deflactor_origen = ''proyectado'' marca meses con IPC estimado, sujetos a revisión.';
