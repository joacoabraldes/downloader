# ETLs mensuales → Supabase (granos · cemento · automotriz)

Monorepo de ETLs de series mensuales argentinas. Un **núcleo compartido** + un paquete por
serie, todo detrás de un solo CLI (`python -m etl ...`). Modelo de datos **append-only**
(cada corrida guarda un snapshot, nunca pisa) con deduplicación, y **desestacionalización
Census X-13** reutilizable. La base es el proyecto **`afcp_cemento`** de Supabase.

## Series

| Comando | Tabla | Fuente histórica | Fuente mensual (incremental) |
|---|---|---|---|
| `granos` | `molienda_granos` | Excel MAGyP | HTML MAGyP (provisorios) |
| `cemento` | `cemento_despacho` | `cemento.xlsx` | HTML AFCP (provisorio/definitivo) |
| `automotriz` | `automotriz` | `ind_automotriz.xlsx` | **PDF ADEFA** (pdfplumber) |

Las tres tablas están en formato **long** (una fila por `serie, date, estado`). Series por
dataset:
- **granos**: `total` (molienda total) + los 7 granos `soja`, `girasol`, `lino`, `mani`,
  `algodon`, `cartamo`, `canola`.
- **cemento**: `despacho_nacional` + `exportacion`, `consumo_despacho_nacional`,
  `importaciones_propias` (estas 3 solo se llenan en los `definitivo`).
- **automotriz**: `produccion`, `ventas` (mayoristas), `expo`.

La desest X-13 corre sobre la **serie principal** de cada dataset (granos `total`, cemento
`despacho_nacional`) y, en automotriz, sobre las 3.

## Estructura del repo

```
etl/
  core/        db.py (conexión + insert/dedup genérico)  ·  seasonal.py (X-13)
  datasets/<serie>/
       source.py       scraping/parsing de la fuente
       load_history.py carga histórica (one-off, desde Excel)
       run.py          ETL incremental + desestacionalización
       config.py       tabla/columnas de la serie
       schema.sql      DDL de la serie (tabla + índices + vistas)
  __main__.py  initdb.py  export.py
```

## Requisitos

```bash
pip install -r requirements.txt
```
Crear un archivo **`.env`** en la raíz (no se versiona) con la connection string del
*pooler* de Supabase:
```
DATABASE_URL=postgresql://postgres.<ref>:<PASS>@aws-1-<region>.pooler.supabase.com:5432/postgres
X13PATH=/ruta/a/la/carpeta/del/binario/x13as     # opcional, para la desestacionalización
CEMENTO_PROXY=http://usuario:pass@host:puerto     # opcional; salida de cemento por proxy (afcp.info bloquea IPs de datacenter)
```
> En vez de `DATABASE_URL` también se aceptan las variables sueltas
> `PGHOST/PGPORT/PGDATABASE/PGUSER/PGPASSWORD` (o `POSTGRES_*`) — útil cuando la base ya las
> expone en el entorno (p.ej. el server). `CEMENTO_PROXY` evita tener que anteponer
> `HTTPS_PROXY=... ` al comando de cemento: se setea una vez y `python -m etl cemento` lo usa solo.

## 1) Crear las tablas (DDL)

Los **DDL están en `etl/datasets/<serie>/schema.sql`** (uno por serie). Para aplicarlos a
la base apuntada por `DATABASE_URL`:

```bash
python -m etl init-db                 # crea las 3 tablas + sus vistas (idempotente)
python -m etl init-db automotriz      # solo una serie
```
Es idempotente (`create table if not exists` / `create or replace view`): se puede correr
las veces que haga falta.

## 2) Carga histórica (una sola vez por serie)

```bash
python -m etl granos load-history
python -m etl cemento load-history    # requiere cemento.xlsx en etl/datasets/cemento/data/
python -m etl automotriz load-history
```
Inserta el histórico con `estado = NULL`.

## 3) ETL incremental (mensual / cron)

```bash
python -m etl granos                  # baja últimos meses + desestacionaliza
python -m etl cemento --month 2026-04
python -m etl automotriz              # baja el PDF de ADEFA del mes + desestacionaliza
python -m etl automotriz --no-fetch   # solo desestacionalizar (no baja el PDF)
```
Flags comunes: `--month YYYY-MM`, `--months-back N`, `--force`, `--no-desest`.

## 4) Exportar los d11 (serie desestacionalizada) a CSV

```bash
python -m etl export                  # los 3 datasets a CSV en la carpeta actual
python -m etl export automotriz       # solo automotriz -> automotriz_d11.csv
python -m etl export automotriz --dir ~/csvs
```
`automotriz_d11.csv` sale en formato ancho: `date, produccion, ventas, expo`.

## Modelo de datos

Cada tabla es **append-only**: una corrida inserta un snapshot nuevo (con `ingested_at`)
solo si el valor es nuevo o cambió respecto del último de ese `(clave, estado)`. `estado`:
`NULL` = histórico (Excel) · `provisorio`/`definitivo` = fuente mensual · `desestacionalizado`
= X-13. Vistas por dataset:
- `<tabla>_actual`: serie **observada** (último snapshot por `serie, mes`, excluye la desest).
- `<tabla>_desest`: serie **desestacionalizada** (X-13), un valor por `serie, mes`. Incluye
  la columna **`parametros`** (jsonb) con lo que se usó en la corrida (modo mult/add, método,
  etc.), para poder auditar diferencias contra otro cálculo.

Y dos vistas que **homogeneízan el consumo** de los 3 datasets en una sola forma (agregan una
columna `dataset`):
- `series_actual`: serie observada actual de granos + cemento + automotriz.
- `series_desest`: serie desestacionalizada de los 3.

## Desestacionalización (Census X-13)

`etl/core/seasonal.py` arma un `.spc`, ejecuta el binario `x13as` (ruta en `X13PATH`) y lee
la tabla **d11**. Si `X13PATH`/el binario no están, **saltea con aviso** (no rompe el ETL;
útil para correr el resto en Windows y la desest en una VM Linux).

Corre el flujo **X-13ARIMA-SEATS**: preajuste **regARIMA** (modelo ARIMA automático vía
`automdl`) + **ajuste por días hábiles** (`regression{variables=(<td>)}` — las series son de
flujo: un mes con más días laborables produce/vende más) + detección de **outliers**, y
descomposición **X-11** con filtro estacional **s3x5** (leemos d11). El modo es multiplicativo
(`transform=log`) por default, o aditivo (`transform=none`) si la serie tiene algún valor ≤ 0.
El **trading-day es por serie** (`deseasonalize(td=...)`, default `td1coef`): produccion usa
`td1coef` (1 coef) y cemento `td` (6 coef). **Esta config reproduce exacto las referencias del
jefe** (produccion y cemento, error 0; ver `scripts/calibrar.py` y `scripts/calibrar_cemento.py`,
los harness que las reverse-engineerearon). Cada
fila desestacionalizada guarda en **`parametros`** (jsonb) lo usado:
`{metodo, modo, transform, regarima, automdl, outliers, trading_day, seasonalma, tabla,
n_meses, arima}` — `arima` es el modelo que eligió automdl (ej. `(1 1 1)(0 1 1)`), parseado del
`serie.html` (la build HTML no genera `.udg`) anclando en "Final automatic model choice".

**Guardar la salida de X-13 (para auditar / ajustar la serie):** agregá `--x13-out DIR` a
cualquier `run`. Guarda en `DIR/<serie>/` el corrido completo de `x13as`: el `serie.html`
(modelo elegido, factores estacionales, diagnósticos M/Q), las tablas `serie.d10` (factores
estacionales), `serie.d11` (desest), `serie.d12` (tendencia), `serie.d13` (irregular) y el
`serie.spc` usado. Ej.: `python -m etl automotriz --no-fetch --x13-out ~/x13_out`.

> **Modo del X-11**: por defecto **multiplicativo**. Si una serie tiene algún valor ≤ 0
> (p.ej. `produccion` en **abril-2020**, COVID: plantas cerradas, producción 0), el núcleo
> pasa esa serie a **aditivo** automáticamente (el multiplicativo no admite ceros). Por eso
> hoy `produccion` se desestacionaliza en aditivo.

## La fuente de automotriz (ADEFA)

`etl/datasets/automotriz/source.py` baja el informe mensual
(`https://www.adefa.org.ar/upload/estadisticas/resumen-<YYYY>-<MM>-es.pdf`) y, con
`pdfplumber`, lee las 3 cifras del mes (Producción Nacional / Exportaciones / Ventas a
Concesionarios) de la tabla **"Comparativo"** del PDF.
