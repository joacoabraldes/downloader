---
name: downloader-etl
description: "Trigger: downloader repo, ETL, dataset nuevo, ingesta, deflactar, serie real, desestacionalizar, X-13, series_desest.toml, redesest. Cómo ingesta, deflacta y ajusta este repo."
license: MIT
metadata:
  author: "jmtruffa"
  version: "1.0"
---

## Activation Contract

Cargar al tocar `etl/` o `scripts/`, agregar/modificar un dataset o serie, cambiar parámetros X-13, deflactar una serie, o diagnosticar un ETL (`etl_control_salud`, cron, `run_etl.sh`).

## Hard Rules

- Filas observadas: SIEMPRE `db.insert_if_changed` (append-only, dedup por `(clave, estado)`). Nunca UPDATE/DELETE de observados.
- Carril `estado='desestacionalizado'`: SIEMPRE UPSERT sobre el unique index parcial `(serie,date) where estado='desestacionalizado'`. `insert_if_changed` lo violaría.
- Consumir por vistas `<tabla>_actual` / `_desest` / `series_actual` / `series_desest`, nunca la tabla cruda.
- Qué se desestacionaliza y cómo vive SOLO en `etl/series_desest.toml`; no hardcodear parámetros en código.
- X-13 corre sobre la serie REAL cuando hay deflación: apuntar `view = "<tabla>_real"` en el bloque del toml.
- Deflactar en una vista SQL contra `public.deflactores` (`ipc_largo` pesos, `uscpi_mensual` USD NSA). Nunca un deflactor desestacionalizado. Ese origen lo mantiene otro repo (`downloaders_viejos/downloader`).
- Antes de deflactar una serie argentina previa a 1992: cortar en las reformas monetarias (`real_desde`).
- Una serie va en `DESEST_OFICIAL` (organismo) O en el toml, nunca en ambos.
- Fallas: `rep.error()` o `note(failure=True)`; `rep.info()` NO registra falla ni cambia el exit code.
- HTTP con `etl/core/http.py`; meses con `etl/core/meses.py`. Host MAGyP compartido: pausa ≥4 s.
- Tablas nuevas con prefijo `etl_`; docs/comentarios del repo en español.

## Decision Gates

| Caso | Acción |
|---|---|
| Serie nueva en dataset existente, cruda | fila en `config.py` del dataset; nada en el toml |
| ...y se desestacionaliza | sumarla a `desest` del toml (+ `overrides` si difiere) |
| Serie en pesos/USD corrientes | vista `_real` con base móvil por serie; X-13 sobre esa vista |
| Organismo publica la desest oficial | bajarla al carril desest (`fuente`=URL, `parametros.origen`) |
| Agregado de componentes ajustadas | ajuste indirecto (sumar desest), no X-13 sobre el total |
| Índice de precio/valor, stock diario, semanal, intermitente | NO X-13 |
| Series con ceros y mode mult/auto | el núcleo fuerza `add`; declararlo `add` |

## Execution Steps

1. Leer `references/pipeline.md` (mapa verificado con file:line).
2. Dataset nuevo: copiar la forma de `cemento` (simple) o `datos_gob` (star-schema + real). Crear `config.py`, `source.py`, `run.py`, `schema.sql` y registrarlo en `etl/__main__.py`, `etl/initdb.py`, `schema_unified.sql`/`schema_daily.sql`, `etl/schema_control.sql` y la tabla de README/INTEGRATION.
3. `python -m etl init-db <ds>`, backfill (`load-history` o `--all`), `python -m etl <ds>`.
4. Cambio de parámetros X-13: calibrar con `scripts/calibrar_<ds>.py` o `--x13-out DIR`, luego `python -m etl redesest <ds>`.
5. Verificar en la base (`etl_control_salud`, `<tabla>_desest.parametros`) antes de declarar terminado.

## Output Contract

Informar: dataset(s) tocados, si la serie es real y con qué deflactor/base, parámetros X-13 y de dónde salen (calibrada / sin referencia), comandos corridos y resultado verificado en la base.

## References

- `references/pipeline.md` — ingesta, deflación, X-13, control y trampas.
- `../../../README.md` — secciones *Desestacionalización*, *Modelo de datos*, fuentes por dataset.
- `../../../INTEGRATION.md` — consumo; `datos_gob` (deflactores, base móvil).
- `../../../etl/series_desest.toml` — cuadro X-13 comentado por serie.
- `../../../help_etl.md` — control de ejecución.
