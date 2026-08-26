-- Escrituras de compraventa de inmuebles en CABA (Colegio de Escribanos de la Ciudad de
-- Buenos Aires), formato LONG. Las 5 series salen del mismo texto del informe mensual.
--
-- OJO CON LAS UNIDADES: no las comparten.
--   compraventa      cantidad de actos                     (unica con cobertura completa)
--   hipotecas        cantidad de actos con hipoteca        (subconjunto de compraventa)
--   monto            PESOS corrientes (el informe publica millones; se guarda x1e6)
--   monto_medio      PESOS corrientes
--   monto_medio_usd  DOLARES, al tipo de cambio oficial promedio del mes segun la fuente
--
-- Las dos series en pesos son NOMINALES: bajo inflacion argentina no se comparan mes contra
-- mes sin deflactar. Se cumple monto / compraventa = monto_medio con mediana 0,0009% de error
-- sobre 117 meses, asi que sirve de control cruzado.
--
-- Las 4 series secundarias tienen HUECOS (109-120 de 120 meses): dependen de como estaba
-- redactado el informe de ese mes. Un mes sin valor simplemente no tiene fila para esa serie.
--
-- ANOMALIA CONOCIDA en monto_medio_usd (2020-11). El cociente monto_medio / monto_medio_usd
-- reconstruye el tipo de cambio y da una serie coherente (14,8 en feb-2016; 1509,6 en jul-2026,
-- con el salto de dic-2023 en su lugar) SALVO en noviembre-2020, donde da 192 $/USD contra ~80
-- del oficial. El texto del informe dice "$15.367.022 (79.920 dolares de acuerdo al tipo de
-- cambio oficial)": el parseo es correcto y la inconsistencia es de la fuente -- probablemente
-- uso el paralelo, que por esos dias rondaba 190. El valor se guarda TAL COMO SE PUBLICO; no se
-- corrige ni se descarta, pero conviene saberlo antes de usar esa serie para inferir el TC.
--
-- Modelo append-only: cada corrida inserta un snapshot nuevo con su ingested_at; nunca se
-- pisa un dato. La fuente revisa: el informe de febrero-2016 dice 1919 actos en el texto del
-- post y 1920 en la planilla de un informe posterior. Son revisiones de ±1, que
-- `insert_if_changed` deja como snapshot nuevo dejando el anterior en la historia.
--
-- Se desestacionaliza SOLO `compraventa` (mult + td1coef + s3x5; ver etl/series_desest.toml).
-- Es la serie con la estacionalidad mas marcada del repo: enero esta 43% abajo de la tendencia
-- y diciembre 34% arriba, y diciembre->enero cae ~55% en crudo todos los anios. X-13 dictamina
-- IDENTIFIABLE SEASONALITY PRESENT con F=53,134** y los 11 estadisticos M en la region de
-- aceptacion. Las otras 4 series quedan crudas: `hipotecas` y `monto_medio_usd` tienen huecos
-- (X-13 exige meses contiguos) y las dos de pesos son nominales.

create table if not exists etl_escrituras_caba (
  id          bigint generated always as identity primary key,
  serie       text   not null check (serie in
                ('compraventa', 'monto', 'hipotecas', 'monto_medio', 'monto_medio_usd')),
  date        date   not null,                 -- primer día del mes al que se refiere el dato
  valor       double precision,                -- unidad SEGUN la serie (ver arriba)
  estado      text,                            -- definitivo (texto del informe) / relleno (planilla de otro informe) / desestacionalizado
  fuente      text,                            -- URL del post o de la planilla .xls
  parametros  jsonb,                           -- solo en desest: parámetros de la corrida X-13
  ingested_at timestamptz not null default now()
);

-- Upgrade idempotente del CHECK de `serie`, para bases creadas cuando el dataset tenía sólo
-- `compraventa`. `create table if not exists` NO toca las constraints de una tabla que ya
-- existe: sin este par la carga de las series nuevas revienta con CheckViolation. El `drop ...
-- if exists` es lo que hace idempotente al `add`.
alter table etl_escrituras_caba drop constraint if exists etl_escrituras_caba_serie_check;
alter table etl_escrituras_caba add constraint etl_escrituras_caba_serie_check
  check (serie in ('compraventa', 'monto', 'hipotecas', 'monto_medio', 'monto_medio_usd'));

-- Búsqueda del último snapshot de un (serie, date, estado).
create index if not exists etl_escrituras_caba_serie_date_estado_idx
  on etl_escrituras_caba (serie, date, estado, ingested_at desc);

-- Una sola fila desestacionalizada por (serie, mes) (UPSERT desde el núcleo X-13).
create unique index if not exists etl_escrituras_caba_desest_uq
  on etl_escrituras_caba (serie, date)
  where estado = 'desestacionalizado';

-- Serie observada "actual" por (serie, mes): último snapshot, excluyendo la desest.
--
-- `definitivo` (el número del texto del informe de ESE mes) le gana a `relleno` (el mismo mes
-- leído en la planilla rodante de un informe POSTERIOR). El relleno existe sólo porque la
-- fuente no publicó informe para 6 meses; si alguna vez aparece el informe propio, su valor
-- pasa a mandar sin que haya que borrar nada.
create or replace view etl_escrituras_caba_actual as
select distinct on (serie, date)
    serie, date, valor, estado, fuente, ingested_at
from etl_escrituras_caba
where estado is distinct from 'desestacionalizado'
order by serie, date,
         (case when estado = 'definitivo' then 0 when estado = 'relleno' then 1 else 2 end),
         ingested_at desc;

-- Serie desestacionalizada (X-13), un valor por (serie, mes). Trae SOLO `compraventa`: las
-- otras 4 series quedan crudas (ver el encabezado y etl/series_desest.toml).
create or replace view etl_escrituras_caba_desest as
select distinct on (serie, date)
    serie, date, valor, fuente, ingested_at, parametros
from etl_escrituras_caba
where estado = 'desestacionalizado'
order by serie, date, ingested_at desc;
