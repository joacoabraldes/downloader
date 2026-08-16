-- Índices de comercio exterior del INDEC (ICA): valor, precio y cantidad, base 2004=100,
-- mensual desde 2004-01. Formato LONG y modelo append-only: cada corrida inserta un snapshot
-- nuevo con su ingested_at; nunca se pisa un dato.
--
-- Star-schema: etl_comex (hechos, serie = slug '<flujo>_<indice>_<rubro>') +
-- etl_comex_series (dimensión, que desarma el slug). El slug plano es la clave porque el
-- cuadro de desestacionalización selecciona por nombre de serie; la dimensión existe para
-- poder filtrar por rubro o por índice sin parsear strings.
--
-- `serie` NO tiene check ni FK a propósito, igual que reservas_pasivos: si INDEC abre un rubro
-- nuevo la ingesta no se rompe (aparece en la vista con nombre NULL, señal de curar el seed).
--
-- Sobre `estado`: INDEC marca los años provisorios con asterisco y los cierra después. Un mes
-- entra primero como 'provisorio' y más tarde vuelve a entrar como 'definitivo' con el mismo
-- valor o con la revisión; los dos snapshots conviven y la vista _actual prefiere el
-- definitivo. Al 2026-08 son provisorios 2024, 2025 y 2026.

create table if not exists etl_comex (
  id          bigint generated always as identity primary key,
  serie       text   not null,             -- '<flujo>_<indice>_<rubro>', ej. 'expo_cantidad_moa'
  date        date   not null,             -- primer día del mes
  valor       double precision,            -- número índice base 2004=100
  estado      text,                        -- provisorio / definitivo (INDEC) / desestacionalizado (X-13)
  fuente      text,                        -- URLs de las planillas / 'census x13'
  parametros  jsonb,                       -- solo en desest: parámetros de la corrida X-13
  ingested_at timestamptz not null default now()
);

create index if not exists etl_comex_serie_date_estado_idx
  on etl_comex (serie, date, estado, ingested_at desc);

-- Una sola fila desestacionalizada por (serie, mes) (UPSERT desde el núcleo X-13).
create unique index if not exists etl_comex_desest_uq
  on etl_comex (serie, date)
  where estado = 'desestacionalizado';

-- Dimensión: el slug desarmado en sus tres ejes + nombre legible. Seed completo (las 18 series
-- que publican las dos planillas). Idempotente vía upsert.
create table if not exists etl_comex_series (
  serie   text primary key,
  flujo   text not null,                   -- 'expo' / 'impo'
  indice  text not null,                   -- 'valor' / 'precio' / 'cantidad'
  rubro   text not null,                   -- 'general' / 'primarios' / 'moa' / 'moi' / 'combustibles'
  nombre  text not null,                   -- nombre legible
  unidad  text,                            -- siempre 'índice 2004=100'
  orden   int                              -- orden de presentación
);

insert into etl_comex_series (serie, flujo, indice, rubro, nombre, unidad, orden) values
  ('expo_valor_general','expo','valor','general','Exportaciones — Nivel general — Índice de valor','índice 2004=100',1),
  ('expo_precio_general','expo','precio','general','Exportaciones — Nivel general — Índice de precio','índice 2004=100',2),
  ('expo_cantidad_general','expo','cantidad','general','Exportaciones — Nivel general — Índice de cantidad','índice 2004=100',3),
  ('expo_valor_primarios','expo','valor','primarios','Exportaciones — Productos primarios — Índice de valor','índice 2004=100',4),
  ('expo_precio_primarios','expo','precio','primarios','Exportaciones — Productos primarios — Índice de precio','índice 2004=100',5),
  ('expo_cantidad_primarios','expo','cantidad','primarios','Exportaciones — Productos primarios — Índice de cantidad','índice 2004=100',6),
  ('expo_valor_moa','expo','valor','moa','Exportaciones — Manufacturas de origen agropecuario (MOA) — Índice de valor','índice 2004=100',7),
  ('expo_precio_moa','expo','precio','moa','Exportaciones — Manufacturas de origen agropecuario (MOA) — Índice de precio','índice 2004=100',8),
  ('expo_cantidad_moa','expo','cantidad','moa','Exportaciones — Manufacturas de origen agropecuario (MOA) — Índice de cantidad','índice 2004=100',9),
  ('expo_valor_moi','expo','valor','moi','Exportaciones — Manufacturas de origen industrial (MOI) — Índice de valor','índice 2004=100',10),
  ('expo_precio_moi','expo','precio','moi','Exportaciones — Manufacturas de origen industrial (MOI) — Índice de precio','índice 2004=100',11),
  ('expo_cantidad_moi','expo','cantidad','moi','Exportaciones — Manufacturas de origen industrial (MOI) — Índice de cantidad','índice 2004=100',12),
  ('expo_valor_combustibles','expo','valor','combustibles','Exportaciones — Combustibles y energía — Índice de valor','índice 2004=100',13),
  ('expo_precio_combustibles','expo','precio','combustibles','Exportaciones — Combustibles y energía — Índice de precio','índice 2004=100',14),
  ('expo_cantidad_combustibles','expo','cantidad','combustibles','Exportaciones — Combustibles y energía — Índice de cantidad','índice 2004=100',15),
  ('impo_valor_general','impo','valor','general','Importaciones — Nivel general — Índice de valor','índice 2004=100',16),
  ('impo_precio_general','impo','precio','general','Importaciones — Nivel general — Índice de precio','índice 2004=100',17),
  ('impo_cantidad_general','impo','cantidad','general','Importaciones — Nivel general — Índice de cantidad','índice 2004=100',18)
on conflict (serie) do update set
  flujo = excluded.flujo, indice = excluded.indice, rubro = excluded.rubro,
  nombre = excluded.nombre, unidad = excluded.unidad, orden = excluded.orden;

-- Serie observada "actual" por (serie, mes): último snapshot, excluyendo la desest, enriquecida
-- con la dimensión. El definitivo del INDEC tiene prioridad sobre el provisorio.
create or replace view etl_comex_actual as
select distinct on (c.serie, c.date)
    c.serie, d.flujo, d.indice, d.rubro, d.nombre, d.unidad,
    c.date, c.valor, c.estado, c.fuente, c.ingested_at
from etl_comex c
left join etl_comex_series d on d.serie = c.serie
where c.estado is distinct from 'desestacionalizado'
order by c.serie, c.date,
         (case when c.estado = 'definitivo' then 0 when c.estado = 'provisorio' then 1
               when c.estado is null then 2 else 3 end),
         c.ingested_at desc;

-- Serie desestacionalizada (X-13), un valor por (serie, mes). Sólo las 6 de cantidad.
create or replace view etl_comex_desest as
select distinct on (c.serie, c.date)
    c.serie, d.flujo, d.rubro, d.nombre,
    c.date, c.valor, c.fuente, c.ingested_at, c.parametros
from etl_comex c
left join etl_comex_series d on d.serie = c.serie
where c.estado = 'desestacionalizado'
order by c.serie, c.date, c.ingested_at desc;
