-- Índice de Confianza en el Gobierno (UTDT), formato LONG. Todas las series en escala 0-5:
--
--   icg                  serie principal, de la planilla .xls de UTDT (fuente = URL del .xls)
--   icg_<componente>     los 5 componentes: eval_gob, benef_gob, adm_gp, cor_gob, resol_prob
--   icg_zona_*           ICG por zona: caba, bsas (provincia), interior
--   icg_sexo_*           ICG por sexo: femenino, masculino
--   icg_edad_*           ICG por edad: 18_29, 30_49, 50_mas
--   icg_edu_*            ICG por nivel educativo: primario, secundario, terciario_univ
--
-- Las aperturas (todo menos `icg`) se calculan desde los microdatos .dta que UTDT publica en
-- la misma página (fuente = URL del .dta): media ponderada con ponderacion_UTDT por ola. Ver
-- etl/datasets/icg/source_microdatos.py.
--
-- Modelo append-only: cada corrida inserta un snapshot nuevo con su ingested_at; nunca se
-- pisa un dato. La planilla de UTDT se republica entera todos los meses, así que
-- `insert_if_changed` deja cada revisión como snapshot nuevo y el anterior queda en la historia.
--
-- Todo entra con estado='definitivo': UTDT publica un solo dato por mes, no hay provisorio.
--
-- Todas las series son continuas desde 2001-11, sin huecos.

create table if not exists etl_icg (
  id          bigint generated always as identity primary key,
  serie       text   not null check (serie in
                ('icg',
                 'icg_eval_gob', 'icg_benef_gob', 'icg_adm_gp', 'icg_cor_gob', 'icg_resol_prob',
                 'icg_zona_caba', 'icg_zona_bsas', 'icg_zona_interior',
                 'icg_sexo_femenino', 'icg_sexo_masculino',
                 'icg_edad_18_29', 'icg_edad_30_49', 'icg_edad_50_mas',
                 'icg_edu_primario', 'icg_edu_secundario', 'icg_edu_terciario_univ')),
  date        date   not null,                 -- primer día del mes
  valor       double precision,                -- índice (escala 0-5)
  estado      text,                            -- definitivo (planilla / microdatos UTDT) / desestacionalizado (X-13)
  fuente      text,                            -- URL del .xls (icg) o del .dta (aperturas) / 'census x13'
  parametros  jsonb,                           -- solo en desest: parámetros de la corrida X-13
  ingested_at timestamptz not null default now()
);
-- Upgrade idempotente para bases ya creadas sin esta columna.
alter table etl_icg add column if not exists parametros jsonb;

-- Upgrade idempotente del CHECK de `serie`, para bases creadas cuando el dataset tenía sólo
-- `icg`. `create table if not exists` NO toca las constraints de una tabla que ya existe: sin
-- este par la carga de las aperturas revienta con CheckViolation. El `drop ... if exists` es
-- lo que hace idempotente al `add`.
alter table etl_icg drop constraint if exists etl_icg_serie_check;
alter table etl_icg add constraint etl_icg_serie_check
  check (serie in ('icg',
    'icg_eval_gob', 'icg_benef_gob', 'icg_adm_gp', 'icg_cor_gob', 'icg_resol_prob',
    'icg_zona_caba', 'icg_zona_bsas', 'icg_zona_interior',
    'icg_sexo_femenino', 'icg_sexo_masculino',
    'icg_edad_18_29', 'icg_edad_30_49', 'icg_edad_50_mas',
    'icg_edu_primario', 'icg_edu_secundario', 'icg_edu_terciario_univ'));

-- Búsqueda del último snapshot de un (serie, date, estado).
create index if not exists etl_icg_serie_date_estado_idx
  on etl_icg (serie, date, estado, ingested_at desc);

-- Una sola fila desestacionalizada por (serie, mes) (UPSERT desde el núcleo X-13).
create unique index if not exists etl_icg_desest_uq
  on etl_icg (serie, date)
  where estado = 'desestacionalizado';

-- Serie observada "actual" por (serie, mes): último snapshot, excluyendo la desest.
create or replace view etl_icg_actual as
select distinct on (serie, date)
    serie, date, valor, estado, fuente, ingested_at
from etl_icg
where estado is distinct from 'desestacionalizado'
order by serie, date, ingested_at desc;

-- Serie desestacionalizada (X-13), un valor por (serie, mes). Hoy queda vacía: el ICG no se
-- desestacionaliza (ver etl/series_desest.toml). La vista existe para que el dataset tenga la
-- misma forma que los demás y sumarlo sea sólo agregar el bloque en el toml.
create or replace view etl_icg_desest as
select distinct on (serie, date)
    serie, date, valor, fuente, ingested_at, parametros
from etl_icg
where estado = 'desestacionalizado'
order by serie, date, ingested_at desc;
