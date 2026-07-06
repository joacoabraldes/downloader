-- Reservas internacionales y principales pasivos del BCRA (archivo diario diar_bas.xls).
-- Dataset DIARIO (date = fecha real, NO primer día del mes) y de stock/nivel: sin X-13.
-- Modelo append-only: cada corrida inserta un snapshot con su ingested_at; nunca pisa un dato.
--
-- Star-schema: etl_reservas_pasivos (hechos, serie = cd_serie del BCRA) + etl_reservas_pasivos_series (dimensión
-- con los nombres legibles). serie NO tiene check ni FK a propósito: si el BCRA agrega una serie,
-- la ingesta no se rompe y aparece en la vista con nombre NULL (señal de que hay que curar el seed).

create table if not exists etl_reservas_pasivos (
  id          bigint generated always as identity primary key,
  serie       text   not null,            -- cd_serie del BCRA (clave del archivo; ver dimensión)
  date        date   not null,            -- fecha diaria real (NO primer día del mes)
  valor       double precision,
  estado      text,                       -- 'definitivo' (BCRA) / NULL
  fuente      text,                        -- URL del diar_bas.xls
  ingested_at timestamptz not null default now()
);

create index if not exists etl_reservas_pasivos_serie_date_estado_idx
  on etl_reservas_pasivos (serie, date, estado, ingested_at desc);

-- Dimensión / catálogo de series: nombres legibles, unidad y grupo. Seed curado (bootstrapeado
-- una vez desde el encabezado del Excel y limpiado a mano). Idempotente vía upsert.
create table if not exists etl_reservas_pasivos_series (
  cd_serie text primary key,              -- id de serie del BCRA (246, 247, ... 8843)
  codigo   text,                          -- código bas* del Excel (solo referencia; NO único)
  nombre   text not null,                 -- nombre legible
  unidad   text,                          -- 'USD millones' / 'ARS millones' / 'tipo de cambio'
  grupo    text,                          -- 'reservas' / 'pasivos' / 'tipo_cambio'
  orden    int                            -- orden de columna en el Excel
);

insert into etl_reservas_pasivos_series (cd_serie, codigo, nombre, unidad, grupo, orden) values
  ('246','bas1','Reservas internacionales — Total','USD millones','reservas',1),
  ('247','bas33','Reservas internacionales — Oro, divisas, colocaciones a plazo y otros','USD millones','reservas',2),
  ('248','bas35','Reservas internacionales — Integración de requisitos de liquidez (Com. "A" 2350) en corresponsalías del exterior','USD millones','reservas',3),
  ('249','bas7','Pasivos monetarios del BCRA — Total','ARS millones','pasivos',4),
  ('250','bas8','Base monetaria — Total','ARS millones','pasivos',5),
  ('251','bas16','Circulación monetaria — Total','ARS millones','pasivos',6),
  ('3743','bas43','Cheques cancelatorios en moneda nacional','ARS millones','pasivos',7),
  ('252','bas17','Cuenta corriente en pesos en el BCRA — Total','ARS millones','pasivos',8),
  ('253','bas18','Cuenta corriente en pesos en el BCRA — Fondos para el pago de O.P.P.','ARS millones','pasivos',9),
  ('254','bas19','Cuenta corriente en pesos en el BCRA — Resto','ARS millones','pasivos',10),
  ('255','bas20','Cuenta corriente en pesos en el BCRA — Fondo de liquidez bancaria','ARS millones','pasivos',11),
  ('256','bas9','Depósitos y otros pasivos monetarios en moneda extranjera en el BCRA — Total','USD millones','pasivos',12),
  ('257',NULL,'Depósitos en moneda extranjera en el BCRA — Fondo de liquidez bancaria','USD millones','pasivos',13),
  ('258',NULL,'Depósitos en moneda extranjera en el BCRA — Cuenta corriente','USD millones','pasivos',14),
  ('3744','bas44','CEDIN y cheques cancelatorios en moneda extranjera','USD millones','pasivos',15),
  ('259',NULL,'Lebac, Nobac y otras (valores efectivos de colocación) — En moneda nacional — Saldo','ARS millones','pasivos',16),
  ('260',NULL,'Lebac, Nobac y otras — En moneda nacional — Monto efectivo colocado','ARS millones','pasivos',17),
  ('261',NULL,'Lebac, Nobac y otras — En moneda nacional — Monto vencimiento','ARS millones','pasivos',18),
  ('262',NULL,'Lebac, Nobac y otras — En dólares estadounidenses — Saldo','USD millones','pasivos',19),
  ('263',NULL,'Lebac, Nobac y otras — En dólares estadounidenses — Monto efectivo colocado','USD millones','pasivos',20),
  ('264',NULL,'Lebac, Nobac y otras — En dólares estadounidenses — Monto vencimiento','USD millones','pasivos',21),
  ('7915','bas45','LELIQ y NOTALIQ — En moneda nacional — Saldo','ARS millones','pasivos',22),
  ('7916','bas46','LELIQ y NOTALIQ — En moneda nacional — Monto efectivo colocado','ARS millones','pasivos',23),
  ('7917','bas47','LELIQ y NOTALIQ — En moneda nacional — Monto vencimiento','ARS millones','pasivos',24),
  ('7918','bas48','NOCOM','ARS millones','pasivos',25),
  ('265','bas10','Posición neta de pases — Total','ARS millones','pasivos',26),
  ('266','bas11','Posición neta de pases — Pases pasivos','ARS millones','pasivos',27),
  ('267','bas12','Posición neta de pases — Pases activos','ARS millones','pasivos',28),
  ('268','bas13','Letras de liquidez bancaria (LELIB)','ARS millones','pasivos',29),
  ('269','bas13','Depósitos del Gobierno — Total','ARS millones','pasivos',30),
  ('8842','bas42','Depósitos del Gobierno — En moneda nacional','ARS millones','pasivos',31),
  ('8843','bas43','Depósitos del Gobierno — En moneda extranjera','USD millones','pasivos',32),
  ('270','bas13','Redescuentos y adelantos por iliquidez otorgados al sistema financiero','ARS millones','pasivos',33),
  ('271','bas24','Tipo de cambio de valuación (1 u$s =)','tipo de cambio','tipo_cambio',34),
  ('272','bas31','Tipo de cambio de referencia (1 u$s =)','tipo de cambio','tipo_cambio',35),
  ('273','bas32','Monto del rescate de las "Cuasimonedas"','ARS millones','pasivos',36),
  ('274','bas42','Asignaciones DEGs 2009','USD millones','pasivos',37)
on conflict (cd_serie) do update set
  codigo = excluded.codigo, nombre = excluded.nombre, unidad = excluded.unidad,
  grupo = excluded.grupo, orden = excluded.orden;

-- Serie observada "actual" por (serie, date): último snapshot, enriquecida con la dimensión.
create or replace view etl_reservas_pasivos_actual as
select distinct on (r.serie, r.date)
    r.serie as cd_serie, c.codigo, c.nombre, c.unidad, c.grupo,
    r.date, r.valor, r.estado, r.fuente, r.ingested_at
from etl_reservas_pasivos r
left join etl_reservas_pasivos_series c on c.cd_serie = r.serie
order by r.serie, r.date, r.ingested_at desc;
