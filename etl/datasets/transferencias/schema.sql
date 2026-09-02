-- Transferencias de automotores (DNRPA), formato LONG: una fila por (serie, date).
-- Hoy hay una sola serie (`autos`); la tabla queda preparada para sumar motos/maquinarias, que
-- la fuente publica con el mismo cuadro (ver config.py).
--
-- Es el mercado de USADOS: cambio de titular de un vehículo que ya está en el parque. No se
-- solapa con `automotriz` (ADEFA: producción/ventas/expo de fábrica) ni con `patentamientos`
-- (SIOMAA: registraciones de 0km).
--
-- Se guarda el TOTAL PAÍS del mes. El cuadro de la DNRPA trae el desagregado por provincia y
-- NO se persiste: son 24 series más que nadie pidió (ver source.py).
--
-- Modelo append-only: cada corrida inserta un snapshot nuevo con su ingested_at; nunca se pisa
-- un dato. La serie desestacionalizada (Census X-13) va como estado='desestacionalizado' con
-- UPSERT (1 fila por serie/mes).

create table if not exists etl_transferencias (
  id          bigint generated always as identity primary key,
  serie       text   not null check (serie in ('autos')),
  date        date   not null,                 -- primer día del mes
  valor       double precision,                -- trámites de transferencia en el mes (total país)
  -- Sólo 'provisorio' y 'desestacionalizado'. NO hay 'definitivo' y no es un olvido: la DNRPA
  -- nunca marca un mes como cerrado y corrige el último hacia arriba a medida que los registros
  -- seccionales terminan de informar (agosto-2026: 155.246 -> 155.717). Sin señal de la fuente,
  -- llamar definitivo a un valor sería inventarlo.
  estado      text   check (estado in ('provisorio','desestacionalizado')),
  fuente      text,                            -- url de la consulta / 'census x13'
  parametros  jsonb,                           -- solo en desest: parámetros de la corrida X-13
  ingested_at timestamptz not null default now()
);
-- Upgrade idempotente para bases ya creadas sin esta columna.
alter table etl_transferencias add column if not exists parametros jsonb;

-- Búsqueda del último snapshot de un (serie, date, estado).
create index if not exists etl_transferencias_serie_date_estado_idx
  on etl_transferencias (serie, date, estado, ingested_at desc);

-- Una sola fila desestacionalizada por (serie, mes) (UPSERT desde el núcleo X-13).
create unique index if not exists etl_transferencias_desest_uq
  on etl_transferencias (serie, date)
  where estado = 'desestacionalizado';

-- Serie observada "actual" por (serie, mes): último snapshot, excluyendo la desest. Acá no
-- hace falta el CASE por prioridad de estado que tienen cemento o patentamientos: todas las
-- filas observadas son 'provisorio' de la misma fuente, así que la más reciente es la buena.
create or replace view etl_transferencias_actual as
select distinct on (serie, date)
    serie, date, valor, estado, fuente, ingested_at
from etl_transferencias
where estado is distinct from 'desestacionalizado'
order by serie, date, ingested_at desc;

-- Serie desestacionalizada (X-13), un valor por (serie, mes).
create or replace view etl_transferencias_desest as
select distinct on (serie, date)
    serie, date, valor, fuente, ingested_at, parametros
from etl_transferencias
where estado = 'desestacionalizado'
order by serie, date, ingested_at desc;
