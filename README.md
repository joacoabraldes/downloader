# ETLs mensuales → Postgres (granos · cemento · automotriz · patentamientos · acero)

Monorepo de ETLs de series mensuales argentinas. Un **núcleo compartido** + un paquete por
serie, todo detrás de un solo CLI (`python -m etl ...`). Modelo de datos **append-only**
(cada corrida guarda un snapshot, nunca pisa) con deduplicación, y **desestacionalización
Census X-13** reutilizable. La base es un **Postgres** (en el servidor: `10.0.16.3/data`).

## Series

| Comando | Tabla | Fuente histórica | Fuente mensual (incremental) |
|---|---|---|---|
| `granos` | `molienda_granos` | Excel MAGyP | HTML MAGyP (provisorios) |
| `cemento` | `cemento_despacho` | `cemento.xlsx` | HTML AFCP (provisorio/definitivo) |
| `automotriz` | `automotriz` | `ind_automotriz.xlsx` | **PDF ADEFA** (pdfplumber) |
| `patentamientos` | `patentamientos` | PDFs SIOMAA (backfill) | **PDF SIOMAA** (pdfplumber) |
| `acero` | `acero` | `Acero.xlsx` (1993→) | **PDF CAA** (scrape + pdfplumber) |

Las tablas están en formato **long** (una fila por `serie, date, estado`). Series por
dataset:
- **granos**: `total` (molienda total) + los 7 granos `soja`, `girasol`, `lino`, `mani`,
  `algodon`, `cartamo`, `canola`.
- **cemento**: `despacho_nacional` + `exportacion`, `consumo_despacho_nacional`,
  `importaciones_propias` (estas 3 solo se llenan en los `definitivo`).
- **automotriz**: `produccion`, `ventas` (mayoristas), `expo`. Es **producción de fábrica**
  (ADEFA), no registraciones.
- **patentamientos**: registraciones 0km del mercado 4W (SIOMAA). Una serie por categoría:
  `total_mercado`, `autos`, `comercial_liviano`, `comercial_pesado`, `otros_pesados`,
  `autos_cl` (autos + C.L.), `autos_cl_cp` (autos + C.L. + C.P.). De la Tabla 1 del informe
  se guardan **solo las unidades del mes**; las variaciones/acumulados son recalculables.
- **acero**: producción de `acero_crudo` (Cámara Argentina del Acero), en miles de toneladas.
  El PDF mensual trae 8 series (arrabio, esponja, laminados, etc.); hoy se ingesta solo acero
  crudo, que es la única con histórico (1993→) y referencia de calibración.

**Qué series se desestacionalizan y con qué parámetros lo define el cuadro central
`etl/series_desest.toml`** (ver la sección *Desestacionalización*): granos **5** series
(`total`, `soja`, `girasol`, `lino`, `mani`), automotriz las 3 (`produccion`, `ventas`,
`expo`), cemento `despacho_nacional`, patentamientos las 7 categorías, acero `acero_crudo`.
`algodon`, `cartamo`
y `canola` **no** se desestacionalizan: su molienda es intermitente (mayormente ceros) y
X-13 no puede ajustarlas; quedan solo como serie observada.

## Estructura del repo

```
etl/
  series_desest.toml   CUADRO: qué series desestacionaliza cada dataset y con qué parámetros X-13
  core/        db.py (conexión + insert/dedup genérico)  ·  seasonal.py (X-13)
               desest_params.py (lee el cuadro y arma los jobs de desest)
  datasets/<serie>/
       source.py       scraping/parsing de la fuente
       load_history.py carga histórica (one-off, desde Excel)
       run.py          ETL incremental + desestacionalización
       config.py       tabla/columnas de la serie
       schema.sql      DDL de la serie (tabla + índices + vistas)
  __main__.py  initdb.py  export.py  redesest.py (recalcular la desest desde la base)
```

## Requisitos

```bash
pip install -r requirements.txt
```
**No hay archivo `.env`.** La configuración va por **variables de entorno**. `db.py` llama a
`load_dotenv()` (por si existiera un `.env`), pero hoy no hay ninguno: todo sale del entorno.
La conexión a Postgres se resuelve en este orden (ver `etl/core/db.py`): **1)** `DATABASE_URL`
si está (no se usa acá); **2)** `PG*`; **3)** `POSTGRES_*`. Se usan las `POSTGRES_*`.

Variables necesarias (host `10.0.16.3`, db `data`):
```
POSTGRES_HOST  POSTGRES_PORT  POSTGRES_DB  POSTGRES_USER  POSTGRES_PASSWORD   # conexión
X13PATH=/ruta/carpeta/del/binario/x13as    # OBLIGATORIO para la desest (X-13); si falta, se saltea con aviso
CEMENTO_PROXY=http://usuario:pass@host:puerto   # cemento sale por proxy (afcp.info bloquea IPs de datacenter)
```
Dónde se definen:
- **Interactivo** (tu shell): exportadas en `~/.bashrc`.
- **Cron**: cron NO sourcea `~/.bashrc`, así que las mismas variables están declaradas en el
  **crontab** (bloque de env arriba de los jobs). Si agregás una var nueva al `.bashrc` que el
  ETL necesite, replicala en el crontab.
> El `cd /home/jmt/dev/downloader` en cada job del cron es obligatorio para que `python -m etl`
> encuentre el paquete y el `.venv` (no para cargar un `.env` — no hay).

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

> **Piso histórico de granos (1993-01).** El Excel de MAGyP arranca en 1965, pero ese tramo
> (prefijo de ceros / cambio de escala) cuelga a X-13. Se recorta la ingesta a **1993-01 en
> adelante** (`config.START_DATE`), aplicado en los dos caminos (`load-history` y `run`), así
> ninguna corrida vuelve a cargar lo anterior. La base ya fue recortada a ese piso. La desest
> además arranca en 2003-01 (`start` en el cuadro), donde las series ya son densas.

## 3) ETL incremental (mensual / cron)

```bash
python -m etl granos                  # baja últimos meses + desestacionaliza
python -m etl cemento --month 2026-04
python -m etl automotriz              # baja el PDF de ADEFA del mes + desestacionaliza
python -m etl patentamientos          # baja el ÚLTIMO informe SIOMAA + desestacionaliza
python -m etl acero                   # baja el último PDF de Cifras de la CAA + desestacionaliza
```
Flags comunes: `--month YYYY-MM`, `--months-back N`, `--force`, `--no-desest`.

> **acero** también scrapea la página de la CAA para encontrar el último PDF de *Cifras* (el
> nombre del archivo es inconsistente, no se puede construir la URL). Cada PDF re-publica los
> últimos 13 meses, así que `run` los reingesta y `insert_if_changed` absorbe las revisiones
> de la CAA. El histórico profundo (desde 1993) sale del Excel: `python -m etl acero load-history`.

> **patentamientos es distinto**: SIOMAA sólo expone el **último** informe gratuito (detrás de
> un flujo de verificación por email), no se puede pedir un mes arbitrario por URL. Por eso su
> `run` es "bajar el último" (sin `--month`/`--months-back`) y el histórico se carga aparte
> desde los PDFs ya bajados: `python -m etl patentamientos load-history --dir CARPETA`.
> Además, SIOMAA publica el dato **final** del mes, así que el mensual va con
> `estado='definitivo'` (no `provisorio`); el histórico del backfill va con `NULL`.

**Cron en el servidor** (idempotente: corre todos los días de la ventana hasta que la fuente
publica; cuando el dato ya está, es un no-op barato). Publican: cemento, automotriz y
patentamientos (SIOMAA) entre el 1 y el 10; granos cerca del 20.
```cron
# ETLs mensuales downloader (idempotentes: corren diario en la ventana hasta que publican)
0  12 1-10  * * cd /home/jmt/dev/downloader && .venv/bin/python -m etl cemento        >> /home/jmt/data/etls/cemento.log 2>&1
10 12 1-10  * * cd /home/jmt/dev/downloader && .venv/bin/python -m etl automotriz     >> /home/jmt/data/etls/automotriz.log 2>&1
0  12 18-31 * * cd /home/jmt/dev/downloader && .venv/bin/python -m etl granos         >> /home/jmt/data/etls/granos.log 2>&1
0  13 1-10  * * cd /home/jmt/dev/downloader && .venv/bin/python -m etl patentamientos >> /home/jmt/data/etls/patentamientos.log 2>&1
```
> El `cd` al repo es obligatorio (para que `python -m etl` encuentre el paquete y el `.venv`).
> Conexión, `X13PATH` y `CEMENTO_PROXY` salen del bloque de env del **crontab** (cron no
> sourcea `.bashrc`; ver *Requisitos*).

Para **recalcular la desestacionalización sin bajar de la web** (p.ej. después de cambiar el
cuadro `etl/series_desest.toml`), ver la sección *Recalcular la desest* más abajo (`redesest`).

## 4) Exportar los d11 (serie desestacionalizada) a CSV

```bash
python -m etl export                  # todos los datasets a CSV en la carpeta actual
python -m etl export automotriz       # solo automotriz -> automotriz_d11.csv
python -m etl export automotriz --dir ~/csvs
```
`automotriz_d11.csv` y `patentamientos_d11.csv` salen en formato ancho (`date` + una columna
por serie); granos y cemento en `date, d11`.

## Modelo de datos

Cada tabla es **append-only**: una corrida inserta un snapshot nuevo (con `ingested_at`)
solo si el valor es nuevo o cambió respecto del último de ese `(clave, estado)`. `estado`:
`NULL` = histórico (Excel) · `provisorio`/`definitivo` = fuente mensual · `desestacionalizado`
= X-13. Vistas por dataset:
- `<tabla>_actual`: serie **observada** (último snapshot por `serie, mes`, excluye la desest).
- `<tabla>_desest`: serie **desestacionalizada** (X-13), un valor por `serie, mes`. Incluye
  la columna **`parametros`** (jsonb) con lo que se usó en la corrida (modo mult/add/auto,
  trading-day, filtro, etc.), para poder auditar diferencias contra otro cálculo.

Y dos vistas que **homogeneízan el consumo** de todos los datasets en una sola forma (agregan
una columna `dataset`):
- `series_actual`: serie observada actual de granos + cemento + automotriz + patentamientos + acero.
- `series_desest`: serie desestacionalizada de los cinco.

## Desestacionalización (Census X-13)

`etl/core/seasonal.py` arma un `.spc`, ejecuta el binario `x13as` (ruta en `X13PATH`) y lee
la tabla **d11**. Si `X13PATH`/el binario no están, **saltea con aviso** (no rompe el ETL;
útil para correr el resto en Windows y la desest en una VM Linux).

Corre el flujo **X-13ARIMA-SEATS**: preajuste **regARIMA** (modelo ARIMA automático vía
`automdl`) + **ajuste por días hábiles** (*trading-day*: las series son de flujo, un mes con más
días laborables produce/vende más) + detección de **outliers**, y descomposición **X-11** con
filtro estacional (leemos d11). Cada corrida recalcula la serie desestacionalizada **entera** (un
dato nuevo re-ajusta todos los meses).

### El cuadro por serie (`etl/series_desest.toml`)

Los parámetros de X-13 **no son globales: son por serie**, y salen del cuadro central
`etl/series_desest.toml` (lo lee `etl/core/desest_params.py`). Cada serie define:
- **`mode`**: `add` (aditivo, `transform=none`, admite ceros) · `mult` (multiplicativo,
  `transform=log`, requiere serie > 0) · `auto` (X-13 elige el modo por AIC).
- **`td`** (trading-day): `td1coef` (1 coef) · `td` (6 coef) · `none` (sin ajuste).
- **`seasonalma`**: filtro estacional del X-11 (`s3x5` estándar).

Parametrización actual (calibrada contra la referencia de cada serie, error ~0):

| Dataset | Series | `mode` | `td` | `seasonalma` |
|---|---|---|---|---|
| granos | las 8 (`total` + 7 granos) | `add` | `none` | `s3x5` |
| automotriz | `produccion`, `expo` | `add` | `td1coef` | `s3x5` |
| automotriz | `ventas` | `auto` | `td` | `s3x5` |
| cemento | `despacho_nacional` | `mult` | `td` | `s3x5` |
| patentamientos | las 7 categorías | `auto` | `td1coef` | `s3x5` |
| acero | `acero_crudo` | `add` | `td1coef` | `s3x5` |

> **patentamientos** aún no tiene referencia de calibración: `mode=auto` deja que X-13 elija
> add/mult por AIC. La desest arranca en **2022-12** (`start` en el cuadro): el informe de
> nov-2022 es pago y falta, y X-13 exige meses contiguos.

> **acero** está calibrado contra la referencia (`Acero.xlsx`, columna `desest`): `add` +
> `td1coef` + `s3x5` reproduce el d11 con error ~0 (máx 0.001% sobre 401 meses). En `auto`
> X-13 converge al mismo modelo.

> **Guard de ceros:** aunque el cuadro diga `mult`/`auto`, si la serie tiene algún valor ≤ 0
> (p.ej. `produccion` en **abril-2020**, COVID: producción 0) el núcleo la fuerza a **aditivo**
> (el X-11 multiplicativo/log no admite ceros).

Cada fila desestacionalizada guarda en **`parametros`** (jsonb) lo usado:
`{metodo, modo, transform, regarima, automdl, outliers, trading_day, seasonalma, tabla,
n_meses, arima}` — `arima` es el modelo que eligió automdl (ej. `(1 1 1)(0 1 1)`), parseado del
`serie.html` (la build HTML no genera `.udg`) anclando en "Final automatic model choice".

### Agregar una serie o un dataset nuevo

Todo se declara en `etl/series_desest.toml` (ver su header):
- **Serie nueva** en un dataset existente → sumá su nombre a `desest` del dataset (debe existir
  en la tabla) y, si difiere del default, agregá un `[<dataset>.overrides.<serie>]`.
- **Dataset nuevo** → un bloque `[<dataset>]` con `table`, `desest` y sus parámetros default.

De esta forma se pueden seguir agregando series para descargar y desestacionalizar sin tocar el
código del núcleo.

### Recalcular la desest (`redesest`)

`python -m etl redesest` recalcula la serie desestacionalizada de cada dataset **desde el
histórico que ya está en la base** (lee `<tabla>_actual`), **sin bajar nada de la web**. Corre
los datasets de corrido. Útil después de cambiar el cuadro.

```bash
python -m etl redesest                 # todos los datasets del cuadro (UPSERT, pisa en el lugar)
python -m etl redesest --clean         # borra las filas 'desestacionalizado' y las regenera
python -m etl redesest granos cemento  # solo esos
python -m etl redesest --x13-out ~/x13_out
```
- **Por default (UPSERT)** pisa cada `(serie, date)` en el lugar; conserva la continuidad por
  fila. Es lo mismo que hace el `run` mensual al final.
- **`--clean`** borra primero (`DELETE ... WHERE estado='desestacionalizado'`) y regenera
  (rebuild limpio): sirve para pizarra limpia y para borrar series huérfanas que salieron del
  cuadro. **Seguridad:** si falta `X13PATH`/el binario, aborta **sin borrar nada**.

**Guardar la salida de X-13 (para auditar / ajustar la serie):** agregá `--x13-out DIR` a
cualquier `run` o a `redesest`. Guarda en `DIR/<serie>/` el corrido completo de `x13as`: el
`serie.html` (modelo elegido, factores estacionales, diagnósticos M/Q), las tablas `serie.d10`
(factores estacionales), `serie.d11` (desest), `serie.d12` (tendencia), `serie.d13` (irregular)
y el `serie.spc` usado. Ej.: `python -m etl redesest automotriz --x13-out ~/x13_out`.

## La fuente de automotriz (ADEFA)

`etl/datasets/automotriz/source.py` baja el informe mensual
(`https://www.adefa.org.ar/upload/estadisticas/resumen-<YYYY>-<MM>-es.pdf`) y, con
`pdfplumber`, lee las 3 cifras del mes (Producción Nacional / Exportaciones / Ventas a
Concesionarios) de la tabla **"Comparativo"** del PDF.

## La fuente de patentamientos (SIOMAA)

`etl/datasets/patentamientos/source.py` descubre el último "Informe de Mercado 4W" gratuito
de la tienda de SIOMAA y lo descarga completando un **flujo de verificación por email**: la
tienda manda un token de 6 dígitos que se recibe con un email temporario (`mail.tm`) y se
devuelve para bajar el ZIP con el PDF. Todo automático, sin intervención.

El parseo de la **Tabla 1** ("Resumen del mercado") es especial: el texto del PDF está
posicionado por coordenadas y sale entreverado con `extract_text()`. Se reconstruye por
posición (chars agrupados por fila `y`, cortados en celdas por gaps de `x`) y de cada fila de
categoría se toma la 1ª cifra = unidades del mes. El mes/año se detectan del encabezado de
columna de la propia tabla (`Ene.2022` / `JUN.26`), robusto a las variantes LITE/full.

El histórico se cargó desde los ~53 PDFs ya bajados (ene-2022 en adelante) con
`load-history --dir`. Esos PDFs **no se versionan** (viven en el repo original de scraping);
para re-hacer el backfill hay que tenerlos a mano.

## La fuente de acero (CAA)

`etl/datasets/acero/source.py` scrapea la página de comunicados de la Cámara Argentina del
Acero (`https://www.acero.org.ar/comunicados-cifras-2023-2-2-2/`) y elige el PDF de *Cifras*
más nuevo (por el mes/año que parsea del nombre; los `CAA-INFORME-*` son prosa y se ignoran).
El PDF de "Producción Siderúrgica Argentina" extrae limpio con `pdfplumber`: cada fila mensual
es `<Mes> <Año>` + 8 valores, de los que se toma **acero crudo** (4ª columna). Cada PDF trae
los últimos ~13 meses, así que el `run` reingesta esa ventana y `insert_if_changed` absorbe
las revisiones que la CAA hace de meses previos. La cifra del PDF se guarda como `definitivo`
(es el número oficial de la CAA); una revisión entra como snapshot `definitivo` nuevo.

El histórico profundo (acero crudo 1993→) sale de `etl/datasets/acero/data/Acero.xlsx`
(`load-history`), cuya columna `desest` es además la **referencia de calibración** de X-13.
