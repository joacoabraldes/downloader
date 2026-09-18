# Pipeline del repo downloader — mapa verificado

Relevado contra el código el 2026-09-18. Si una línea no coincide, manda el código.

## 1. CLI y cron

- `python -m etl <ds> [run|load-history] [flags]` → `etl/__main__.py` importa `etl.datasets.<ds>.run|load_history` y llama `main(argv)`. Los flags pasan tal cual al argparse del módulo.
- También: `init-db [ds...]`, `redesest [ds...] [--clean] [--x13-out DIR]` y `export [ds] [--dir]` (escribe CSV d11).
- `_correr` registra en control SIEMPRE, en un `finally`. `_exit_on_failures` sale con 1 si hay `report.failures()`.
- `scripts/run_etl.sh <ds>` es el wrapper del cron:
  - Usa el `.venv` y loguea en `/home/jmt/data/etls/<ds>.log`.
  - Si falla, manda por stderr la cola de 25 líneas; eso dispara el MAILTO.
- Env: `POSTGRES_*` (orden de prioridad: `DATABASE_URL` → `PG*` → `POSTGRES_*`), `X13PATH` y `CEMENTO_PROXY`.
  - No hay `.env`: en el crontab hay un bloque propio de variables.
- `load_history.py` existe solo en 14 datasets. datos_gob, transferencias, comex, icc, icg y refinacion hacen el backfill con el mismo `run`.

## 2. Contrato de un dataset (`etl/datasets/<ds>/`)

- `config.py`: `TABLE`, `KEY_COLS` (normalmente `["serie","date"]`), `VALUE_COLS` (`["valor"]`), `ACTUAL_VIEW`.
- `source.py`: fetch y parse libres, según la fuente.
- `run.py` / `load_history.py`: exponen `main(argv)`.
- `schema.sql`:
  - Tabla con `estado`, `fuente`, `parametros jsonb` e `ingested_at default now()`.
  - Unique index parcial `(serie,date) where estado='desestacionalizado'`.
  - Vistas `_actual` y `_desest`.
- Registros fuera de la carpeta: `DATASETS` en `__main__.py`, la lista de carril en `initdb.py` (MONTHLY/DAILY/WEEKLY), la UNION en `schema_unified.sql`/`schema_daily.sql`, la fila `(horas_max, dias_max_dato)` en `etl_control_salud` (`schema_control.sql`) y la documentación.

### Flujo típico de run (cemento)

1. `window.target_months(conn, table=...)` arma la ventana: desde min(último mes en la base, hoy−2) hasta el mes actual, con un tope de 24 meses. `--month` o `--months-back` la pisan.
2. Mes por mes:
   - Si el mes ya es `definitivo` (y no hay `--force`), queda `sin_cambios`.
   - Si no, se intenta `provisorio` y después `definitivo`.
   - Cada serie se escribe con `insert_if_changed(estado=..., fuente=url)`.
3. `rep.summary()`.
4. Si `rep.changed` y no hay `--no-desest`: `seasonal.run_desest(conn, ds, desest_params.build_jobs(ds, keep_dir=x13_out))`.

Flags comunes: `--month YYYY-MM`, `--months-back N`, `--force`, `--no-desest`, `--x13-out DIR`.

## 3. Modelo append-only

- `db.insert_if_changed` (db.py:107) compara contra el último snapshot de `(clave, estado)`, con tolerancia 1e-6.
  - Devuelve `nuevo`, `actualizado`, `sin_cambios` o `saltado` (cuando el valor es None).
  - Las columnas `extra` se escriben pero no se comparan.
  - Hace commit por fila.
- Valores de `estado`:
  - `NULL`: histórico del xlsx.
  - `provisorio`, `definitivo`.
  - Propios de un dataset: `relleno` (escrituras), `oficial` (fob), `publicado` (cot).
  - `desestacionalizado`.
- `_actual`: `distinct on (serie,date)` excluyendo la desest.
  - Precedencia: definitivo > NULL > provisorio, y después `ingested_at desc`.
  - datos_gob no tiene CASE: solo `ingested_at desc`.
- `_desest`: último snapshot con `estado='desestacionalizado'`.
- `bulk_insert` no deduplica. Lo usan comex y reservas_pasivos.

## 4. Deflación (serie real)

Hoy hay DOS lugares. Los dos son SQL, no Python.

### datos_gob → vista `etl_datos_gob_real` (datos_gob/schema.sql ~187-231)

- Fórmula: `real = valor * indice_base / indice(t)`.
- `mes_base` = el último mes de CADA serie con nominal y deflactor a la vez. Es base móvil y por serie; con un mes nuevo, la serie real se reescala entera.
- El deflactor lo elige la columna `etl_datos_gob_series.deflactor`, que sale de `SERIES_META` en config.py.
- Hay 11 series deflactadas:

  | deflactor | series |
  |---|---|
  | `ipc_largo` | ventas_supermercados, ventas_centros_compras, ripte, smvm, 5× indice_salarios_* |
  | `uscpi_mensual` (CPI-U NSA) | expo_total, impo_total |

- No se deflactan: `isac`, `ipi_manufacturero`, `ipc_nacional`.
- `REAL_DESDE={"smvm":"1992-01-01"}`: antes de esa fecha no hay real, por las reformas monetarias.
- `origen` = publicado / proyectado / interpolado. Los meses proyectados se revisan.
- `etl_datos_gob_completo` junta nominal, real y desest en una fila.

### fob_granos → PRP (vistas y matviews)

- `ipc_base_1993` = `ipc_largo` dividido por el promedio de 1993.
- `fob_real = fob_usd * tc / ipc_1993` y `prp = fob_real * (1 - dex)`.
- `granos_prp` y `granos_prp_combinado` son materializadas. Se hace REFRESH CONCURRENTLY en cada run (`--no-refresh` lo saltea).
- No pasa por X-13.

### Deliberadamente SIN deflactar

- `escrituras_caba` (`monto`, `monto_medio`): se deja al consumidor.
- `comex`: son índices; el precio ya está separado de la cantidad.

### Patrón para deflactar algo nuevo

1. Vista `<tabla>_real` que haga JOIN con `public.deflactores` y use base móvil por serie.
2. Si la serie empieza antes de 1992, poner `real_desde` en el corte de moneda.
3. Si se desestacionaliza, poner `view = "<tabla>_real"` en el toml.
4. Si el deflactor cambia sin que haya dato nuevo, NO condicionar la desest a `rep.changed`. datos_gob la corre siempre.

## 5. X-13 (Census x13as)

- Cuadro: `etl/series_desest.toml`, leído por `desest_params.build_jobs(ds)`.
  - Obligatorias: `table`, `desest`, `mode` (add/mult/auto), `td` (td1coef/td/none), `seasonalma`.
  - Opcionales: `easter` (default 0; solo transferencias usa 1), `start`, `view` (default `<table>_actual`), `[ds.overrides.<serie>]`.
- `seasonal.deseasonalize` (seasonal.py:179):
  1. Si falta `X13PATH` o el binario: `skipped`, sin error.
  2. Lee `select date, valor from <view> where serie=%s` y recorta a `start`.
  3. Saltea si hay menos de 36 meses o la serie tiene huecos.
  4. Si hay algún valor ≤ 0 con mult/auto, fuerza `add` (queda en `parametros.modo_motivo`).
  5. Arma el `.spc`: transform, regression td[/easter], `automdl{}`, `outlier{}`, `x11{ seasonalma save=(d10 d11 d12 d13) }`.
  6. Corre con timeout de 200 s y lee la tabla d11 y el modelo ARIMA del `serie.html`.
  7. Hace UPSERT `ON CONFLICT (serie,date) WHERE estado='desestacionalizado'` con `fuente='census x13'` y `parametros` jsonb.
- Ajuste indirecto: `gasoil_mas_nafta` es la suma de las desest de gasoil y nafta, en la vista de ventas_combustibles.
- Desest oficial (datos_gob): `DESEST_OFICIAL` para isac e ipi escribe el mismo carril.
  - Ahí `fuente` es la URL y `parametros.origen` es `"indec"`.
  - `run.py` aborta si una serie está también en el toml.
- Qué pasa por X-13:
  - Sí: granos (total/soja/girasol/mani, desde 2003), automotriz, cemento, patentamientos (desde 2022-12), transferencias, acero, aves, leche, bovinos, demanda_energia, hidrocarburos (2 totales), ventas_combustibles (gasoil/nafta/glp/asfaltos), escrituras_caba (compraventa), datos_gob (4, sobre la real), comex (6 de cantidad).
  - No: refinacion, icc, icg, reservas_pasivos, fob_granos, compras_granos, cot.
- `redesest`:
  - Recalcula desde la base sin bajar nada de la web. Por default hace UPSERT.
  - `--clean` borra el carril antes de regenerar. Aborta sin borrar si no está X-13.
  - **Trampa:** `redesest datos_gob --clean` también borra la desest oficial de isac/ipi, y no la regenera. Vuelve con el próximo `python -m etl datos_gob`.
- Para calibrar: `scripts/calibrar_<ds>.py` barre mode×td×seasonalma(×easter) contra la columna `desest` del xlsx de referencia. Los parámetros calibrados y su error están documentados en README.md, sección *Desestacionalización*.

## 6. Control

- `etl_control_ejecucion` guarda una fila por corrida: estado ok/falla, `fallas[]`, contadores y `ultimo_dato`.
- `etl_control_salud` compara cada dataset contra sus `horas_max` y `dias_max_dato`.
  - `estado`: NUNCA_CORRIO / FALLA / SIN_CORRER / ok.
  - `estado_dato`: SIN_DATO / DATO_VIEJO / ok.
- El control es por DATASET: en datos_gob, una serie congelada no dispara la alarma.

## 7. Trampas conocidas

- `schema_unified.sql:123,127` tiene comentarios viejos: escrituras_caba y ventas_combustibles SÍ se desestacionalizan.
- El comentario de `[granos]` en el toml habla de un timeout de 120 s; en el código es 200 s.
- `apis.datos.gob.ar` puede ir meses atrás del INDEC: pegarle a la API antes de dar un ETL por roto.
- Una URL inexistente de INDEC devuelve HTML con 200: validar encabezado y cantidad de filas.
- El repo no tiene tests ni CI. La verificación es contra la base y contra las planillas de referencia.
