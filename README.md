# ETLs mensuales → Postgres (granos · cemento · automotriz · patentamientos · acero · aves · leche · bovinos · demanda_energia · icc · icg · datos_gob · comex)

Monorepo de ETLs de series mensuales argentinas. Un **núcleo compartido** + un paquete por
serie, todo detrás de un solo CLI (`python -m etl ...`). Modelo de datos **append-only**
(cada corrida guarda un snapshot, nunca pisa) con deduplicación, y **desestacionalización
Census X-13** reutilizable. La base es un **Postgres** (en el servidor: `10.0.16.3/data`).

## Series

| Comando | Tabla | Fuente histórica | Fuente mensual (incremental) |
|---|---|---|---|
| `granos` | `etl_molienda_granos` | Excel MAGyP | HTML MAGyP (provisorios) |
| `cemento` | `etl_cemento_despacho` | `cemento.xlsx` | HTML AFCP (provisorio/definitivo) |
| `automotriz` | `etl_automotriz` | `ind_automotriz.xlsx` | **PDF ADEFA** (pdfplumber) |
| `patentamientos` | `etl_patentamientos` | PDFs SIOMAA (backfill) | **PDF SIOMAA** (pdfplumber) |
| `acero` | `etl_acero` | `Acero.xlsx` (1993→) | **PDF CAA** (scrape + pdfplumber) |
| `aves` | `etl_aves` | `Aves.xlsx` (1981→) | **xlsx MAGyP** (scrape) + **PDF** de faena (fallback) |
| `leche` | `etl_leche` | `leche.xlsx` (2015→) | **xlsx MAGyP** (URL fija) |
| `bovinos` | `etl_bovinos` | `Bovinos.xlsx` (1998→) + planilla MAGyP (1990-1997) | **.xls MAGyP** (link dentro de un PDF) |
| `demanda_energia` | `etl_demanda_energia` | `energia.xlsx` (2005→) | **xlsx CAMMESA** (URL fija) |
| `icc` | `etl_icc` | — (la planilla trae 1998→) | **.xls UTDT** (link resuelto por scrape) |
| `icg` | `etl_icg` | — (la planilla trae 2001→) | **.xls UTDT** (link resuelto por scrape) |
| `datos_gob` | `etl_datos_gob` | — (la API trae 1965→) | **API oficial** `apis.datos.gob.ar/series` |
| `comex` | `etl_comex` | — (las planillas traen 2004→) | **2 .xls INDEC** (URLs fijas, `xlrd`) |

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
- **aves**: `faena` avícola (MAGyP, datos SENASA), en miles de cabezas. Histórico 1981→. La
  fuente de indicadores trae además producción/comercio/consumo, que podrían sumarse.
  El xlsx de indicadores se actualiza **más tarde** que el PDF `Faena Avícola <año>.pdf` de la
  misma página, así que los meses que el xlsx todavía no tiene se completan desde el PDF con
  estado `provisorio` (viene redondeado a la unidad: 67.120 vs 67119.556 del xlsx). Cuando el
  xlsx publica el mes entra como `definitivo` y la vista `_actual` lo prioriza sola. El PDF
  nunca pisa un mes que el xlsx ya tenga, ni rellena huecos viejos. Se desactiva con
  `--no-pdf-fallback`.
- **leche**: `produccion` nacional de leche (MAGyP, Dir. Nacional de Lechería), en litros por
  mes. Histórico 2015→.
- **bovinos**: `produccion` de carne bovina (MAGyP, base SENASA), en miles de toneladas res
  con hueso. Histórico 1998→. El xls fuente trae además faena (cabezas), % hembras y peso.
- **demanda_energia**: demanda eléctrica del MEM (CAMMESA), en **GWh**. La serie principal
  `no_residencial` = `no_res_estacionalizada` (Demanda No Residencial) + `no_estacionalizada`
  (grandes usuarios GUDI/GUME/GUMA/MATE). Histórico 2005→ (de `energia.xlsx`); CAMMESA cubre
  2023→. Se guardan además todas las filas del cuadro como series: `estacionalizada`,
  `residencial`, `gudi`, `gume`, `guma`, `mate_distribuidor`, `local`. La fuente está en MWh;
  se convierte a GWh (÷1000). Se parsea **por etiqueta**, no por fila (CAMMESA agrega filas).
- **icc**: Índice de Confianza del Consumidor (UTDT), escala 0-100. 7 series: `nacional`, sus
  3 aperturas geográficas (`capital`, `gba`, `interior`) y sus 3 subíndices
  (`situacion_personal`, `situacion_macro`, `bienes_durables`). `capital` arranca en 1998-07 y
  las otras 6 en 2001-03, cuando el índice pasó a relevarse a nivel nacional. Sin huecos.
  La planilla repite la columna "ICC Nacional" en sus dos hojas y **no coinciden** en 24 meses
  viejos (casi todo redondeo, pero 2009-06 difiere en 0,2): `nacional` se toma siempre de la
  hoja de regiones.
- **icg**: Índice de Confianza en el Gobierno (UTDT), escala 0-5. Una serie (`icg`), continua
  desde 2001-11. La planilla está **traspuesta** (meses en columnas) y partida en dos hojas
  (2001-2022 y 2023→) que empalman sin solaparse.
- **datos_gob**: 14 series de la **API oficial de Series de Tiempo del Estado**
  (`apis.datos.gob.ar/series`): `isac` (construcción), `ipi_manufacturero`, `ipc_nacional`,
  `expo_total` / `impo_total` (comercio exterior en USD), `ventas_supermercados`,
  `ventas_centros_compras`, `ripte`, `smvm` y las 5 aperturas del **índice de salarios** de INDEC
  (`total`, `registrado`, `priv_registrado`, `publico`, `priv_no_registrado`). Es el único
  dataset **star-schema mensual**: sus series no comparten unidad (conviven índices, dólares y
  pesos), así que el nombre y la unidad viven en `etl_datos_gob_series`.
  Las 11 series en valores corrientes tienen además **valor real** en `etl_datos_gob_real`, a
  precios del último dato de cada serie (base móvil y por serie; el mes viaja en `mes_base`).
  Son **dos deflactores**, ambos de `public.deflactores` y elegidos por la columna `deflactor`:
  `ipc_largo` para las 9 en pesos (desde 1990-01) y `uscpi_mensual` —CPI-U del BLS, NSA— para
  `expo_total` / `impo_total`, porque estar en dólares no exime de deflactar. Las 2 de ventas y
  las 2 de comercio exterior se **desestacionalizan sobre esa serie real**.
  `etl_datos_gob_completo` devuelve los tres valores —nominal, real y desestacionalizado— en una
  sola fila.
- **comex**: 18 **números índice** de comercio exterior del INDEC (ICA), **base 2004=100**, desde
  2004-01. Son `valor`, `precio` y `cantidad` para las exportaciones abiertas por **grandes
  rubros** (`general`, `primarios`, `moa`, `moi`, `combustibles`) y para las importaciones a
  nivel general —INDEC no abre las importaciones por grandes rubros en esta serie mensual—.
  El nombre de serie es un slug plano `<flujo>_<indice>_<rubro>` (p. ej. `expo_cantidad_moa`)
  porque el cuadro de desestacionalización selecciona por nombre; los tres ejes viven
  desarmados en la dimensión `etl_comex_series` para poder filtrar sin parsear strings.
  Se desestacionalizan las **6 series de cantidad** (los 5 rubros de expo + impo nivel general):
  son el volumen físico, que es lo que se compara mes contra mes. Precio y valor quedan crudos
  (ver `etl/series_desest.toml`).

> **`comex` no reemplaza a `expo_total` / `impo_total` de `datos_gob`, ni al revés.** Aquéllas
> son montos en **dólares** (y su serie real deflactada por CPI de EEUU); éstas son **números
> índice** que separan el monto en sus dos componentes: cuánto se despachó (`cantidad`) y a qué
> precio (`precio`). Cuando la pregunta es "¿exportamos más *cosas* o sólo subió el precio de la
> soja?", la respuesta está en `expo_cantidad_*`, no en el monto.

> Los dos índices de UTDT son la excepción del repo en dos cosas. **(1)** Se publican *dentro*
> del mes de referencia, no al mes siguiente. **(2)** No se desestacionalizan: UTDT los publica
> crudos, así que no hay referencia contra la cual calibrar X-13 (ver `etl/series_desest.toml`).

> **datos_gob es el único ETL que no scrapea nada.** Es una API, así que sumar una serie es
> agregar una fila a `SERIES_META` en su `config.py` — no se escribe código. La evaluación de la
> fuente, incluido **por qué NO reemplaza a ninguno de los otros scrapers**, está en
> `docs/datos_gob_ar.md`.

### Series diarias (BCRA)

Además de los datasets mensuales de arriba, hay un **carril diario** (primer y único por ahora):

| Comando | Tabla | Dimensión (nombres) | Fuente |
|---|---|---|---|
| `reservas_pasivos` | `etl_reservas_pasivos` | `etl_reservas_pasivos_series` | **.xls BCRA** `diar_bas.xls` (URL fija, `xlrd`) |

`reservas_pasivos` trae las **37 series** del archivo *Información sobre reservas internacionales y
principales pasivos del BCRA* (reservas en USD + principales pasivos en pesos + tipos de cambio),
**diarias** desde 1996. Es un dataset distinto al resto: `date` es la **fecha diaria real** (no el
primer día del mes), **no se desestacionaliza** (es stock/nivel), y `serie` es el **`cd_serie`**
del BCRA (clave estable del archivo). Los nombres legibles, la unidad y el grupo de cada serie
viven en la dimensión `etl_reservas_pasivos_series`; la vista `etl_reservas_pasivos_actual` los une por JOIN. El
consumo transversal va por `series_diarias_actual` (carril separado de `series_actual`, que sigue
siendo 100% mensual). Ver `INTEGRATION.md`, sección *Series diarias*.

> El parser saltea las filas **aún no publicadas** (el BCRA carga la fila del día con casi todo en
> 0): usa las reservas totales (`cd 246`, nunca 0 en 30 años) como ancla. Los ceros legítimos de
> las demás series (p.ej. un monto de vencimiento sin vencimiento ese día) se conservan.

### Series semanales (MAGyP)

Tercer carril: **semanal**. Hoy tiene un solo dataset.

| Comando | Tabla | Vista | Fuente |
|---|---|---|---|
| `compras_granos` | `etl_compras_granos` | `etl_compras_granos_actual` | **HTML MAGyP**, una página por semana |

`compras_granos` trae el informe semanal de **compras de granos y DJVE**: por cultivo (trigo, maíz,
sorgo, cebada cervecera, cebada forrajera, soja, girasol), campaña, **sector** (exportador /
industria / total) y métrica (semanal, total comprado, precio hecho, a fijar, fijado, saldo a
fijar, DJVE acumulada). En **miles de toneladas**, desde **marzo de 2005**. `date` es la fecha de
corte del informe (miércoles), **no** el primer día del mes, y **no se desestacionaliza**: mezcla
flujo semanal con acumulados de campaña. Por eso no entra en `series_actual`.

La fuente **no publica ningún archivo descargable**: el histórico son ~1.100 páginas HTML, una por
semana, y se navega índice de años → índice de semanas → página semanal. Los links semanales van
por `javascript:window.open(...)`, así que se extraen del HTML crudo. Se scrapea el índice en vez
de generar los miércoles: la fuente saltea semanas y algún link apunta a una página inexistente.

> El HTML cambió de formato **dos veces** (2005-2016, 2017-2018, 2019→) y `source.py` tiene un
> parser por familia. Los tres difieren en qué métricas publican: antes de 2017 no hay DJVE (sí
> "embarque estimado acumulado", que es otro concepto) y en los primeros años hay ventas
> potenciales/efectivas. Por eso la tabla es **long por métrica**: cada formato emite su
> subconjunto sin columnas fantasma ni `ALTER TABLE` en el próximo cambio de la fuente.

**Qué series se desestacionalizan y con qué parámetros lo define el cuadro central
`etl/series_desest.toml`** (ver la sección *Desestacionalización*): granos **4** series
(`total`, `soja`, `girasol`, `mani`), automotriz las 3 (`produccion`, `ventas`,
`expo`), cemento `despacho_nacional`, patentamientos las 7 categorías, acero `acero_crudo`,
aves `faena`, demanda_energia `no_residencial`, comex las **6 de cantidad**
(`expo_cantidad_*` + `impo_cantidad_general`). `lino`, `algodon`, `cartamo`
y `canola` **no** se desestacionalizan: su molienda es intermitente (mayormente ceros) y
X-13 no puede ajustarlas; quedan solo como serie observada.

## Estructura del repo

```
etl/
  series_desest.toml   CUADRO: qué series desestacionaliza cada dataset y con qué parámetros X-13
  schema_unified.sql   vistas series_actual / series_desest (unen los datasets mensuales)
  schema_daily.sql     vista series_diarias_actual (une los datasets diarios; hoy solo reservas_pasivos)
  core/        db.py (conexión + insert/dedup genérico)  ·  seasonal.py (X-13)
               desest_params.py (lee el cuadro y arma los jobs de desest)
               window.py (ventana de meses del incremental)  ·  report.py (salida uniforme)
               meses.py (nombres y abreviaturas de mes en español, con sus variantes)
  datasets/<dataset>/
       source.py       scraping/parsing de la fuente (HTML / PDF / xlsx / .xls, según el dataset)
       load_history.py carga histórica (one-off, desde el Excel de referencia)
       run.py          ETL incremental + desestacionalización
       config.py       tabla/columnas de la serie
       schema.sql      DDL de la serie (tabla + índices + vistas)
       data/           Excel de referencia/histórico (donde aplica)
  __main__.py  initdb.py  export.py  redesest.py (recalcular la desest desde la base)
scripts/       harness de calibración X-13 por serie (calibrar_<dataset>.py) y comparaciones
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
python -m etl init-db                 # crea todas las tablas + sus vistas (idempotente)
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
python -m etl aves                    # baja el último xlsx de indicadores de MAGyP + desestacionaliza
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
publica; cuando el dato ya está, es un no-op barato). Publican: cemento y patentamientos
(SIOMAA) entre el 1 y el 10; granos cerca del 20; acero (CAA) **sin día
confirmado** (ver nota abajo); aves (MAGyP) entre el 20 y el 31; leche (MAGyP) entre el 20
y el 10; demanda_energia (CAMMESA) publica con ~1 mes de rezago y **sin fecha previsible**
(actualiza el mismo archivo, `wpdmdl` fijo), así que corre todos los días. **automotriz** está en
el mismo caso: ADEFA no tiene fecha de publicación previsible y julio-2026 salió después del día
10, así que la ventana 1-10 lo perdió y el dato hubiera entrado recién el 1 de septiembre; por eso
ahora corre todos los días, sin ventana. Los dos de UTDT son
la excepción: publican **dentro del mes de referencia** según un cronograma anual — el ICC un
jueves (día 17 al 24) y el ICG un lunes (día 22 al 28).
```cron
# ETLs mensuales downloader (idempotentes: corren diario en la ventana hasta que publican)
15  9 1-10      * * /home/jmt/dev/downloader/scripts/run_etl.sh cemento
# automotriz: ventana 1-10 desde el 14-ago-2026. OJO con el historial: ADEFA publica sin fecha
# previsible y julio-2026 salio DESPUES del dia 10, con lo que esta misma ventana lo perdio --
# por eso habia pasado a diario. Con 1-10 ese caso entra recien el 1 del mes siguiente, ~3
# semanas tarde, y el respaldo por gacetillas no lo salva: adelanta el dato solo si el ETL corre.
# Si vuelve a pasar, revertir a `10 12 * * *` y volver horas_max a 80.
10 12 1-10      * * /home/jmt/dev/downloader/scripts/run_etl.sh automotriz
0  12 18-31     * * /home/jmt/dev/downloader/scripts/run_etl.sh granos
0  13 1-10      * * /home/jmt/dev/downloader/scripts/run_etl.sh patentamientos
# acero: ventana ancha a proposito. NO sabemos el dia real de publicacion de la CAA: la unica
# publicacion observada es junio/2026, que aparecio el 31-jul (al 4-jul lo ultimo publicado era
# mayo). Con un solo dato no se puede acotar, y la corrida es un no-op de segundos. Cuando haya
# 2-3 fechas observadas, cerrar la ventana y bajar `horas_max` en etl/schema_control.sql en el
# mismo cambio.
0  10 15-31,1-10 * * /home/jmt/dev/downloader/scripts/run_etl.sh acero
0  11 20-31     * * /home/jmt/dev/downloader/scripts/run_etl.sh aves
0  14 20-31,1-10 * * /home/jmt/dev/downloader/scripts/run_etl.sh leche
# bovinos: sin ventana. MAGyP movio la fecha de publicacion (junio salio el 20-jul y julio el
# 7-ago), con lo que la ventana 20-31 lo perdia.
0  11 *         * * /home/jmt/dev/downloader/scripts/run_etl.sh bovinos
0  12 *         * * /home/jmt/dev/downloader/scripts/run_etl.sh demanda_energia
# UTDT publica DENTRO del mes de referencia, con cronograma anual: ICC un jueves (dia 17 al 24),
# ICG un lunes (dia 22 al 28). Las ventanas arrancan antes del primer dia posible y llegan a fin
# de mes; la corrida es idempotente y repite hasta que la planilla trae el mes nuevo.
30 13 17-31     * * /home/jmt/dev/downloader/scripts/run_etl.sh icc
0  19 22-31     * * /home/jmt/dev/downloader/scripts/run_etl.sh icg
# datos_gob: 9 series de organismos distintos, cada uno con su calendario. Sin ventana; la
# corrida son 9 requests a una API y es idempotente.
45 15 *         * * /home/jmt/dev/downloader/scripts/run_etl.sh datos_gob
# comex: INDEC publica el ICA a mediados del mes siguiente (junio-2026 quedo en las planillas el
# 20-jul). Ventana 18-31, igual que granos. La corrida re-lee siempre los meses de los anios que
# INDEC todavia marca provisorios, asi que ademas capta las revisiones sin pedirselo.
30 16 18-31     * * /home/jmt/dev/downloader/scripts/run_etl.sh comex
# reservas_pasivos: dos pasadas por dia habil, 10:00 y 16:30 (pedido del usuario, 16/08/2026).
# OJO: el BCRA sube diar_bas.xls a la TARDE -- ~18:22 segun el `Last Saved` del archivo del
# viernes 14-ago -- asi que NINGUNA de las dos pasadas ve la publicacion del dia: el dato entra
# recien a la mañana siguiente. El horario anterior (19:15 + 20:30) la agarraba con ~1 h de
# atraso; las 30 fechas medidas habian entrado TODAS a las 19:15, ninguna a las 20:30.
# Costo medido: ~15 h mas de lag por dia habil, ~3 dias si publica un viernes. Por eso
# dias_max_dato paso de 8 a 11. Para revertir: `15 19` + `30 20` y dias_max_dato a 8.
0  10 *         * 1-5 /home/jmt/dev/downloader/scripts/run_etl.sh reservas_pasivos
30 16 *         * 1-5 /home/jmt/dev/downloader/scripts/run_etl.sh reservas_pasivos
# Semanal (compras y DJVE de granos). Corre todos los dias habiles porque TODAVIA NO SABEMOS
# el dia real de publicacion: la pagina dice "se actualiza los miercoles" pero no esta verificado.
# Es idempotente y barato (baja ~4 paginas y re-lee las ultimas 3 semanas, con lo que ademas
# capta las revisiones), asi que correr de mas no cuesta nada. Cuando se confirme el dia, acotar
# la ventana y bajar `horas_max` en etl/schema_control.sql en el mismo cambio.
0  10 *         * 1-5 /home/jmt/dev/downloader/scripts/run_etl.sh compras_granos
```
> Los jobs pasan por **`scripts/run_etl.sh`**, que hace el `cd` al repo, escribe
> `/home/jmt/data/etls/<dataset>.log` y —sólo si la corrida falla— repite el final por stderr
> para que dispare el mail del `MAILTO`. **No agregarles `>> log 2>&1`**: eso se traga el aviso
> y es exactamente el bug que el wrapper viene a resolver (ver *Fallas y código de salida*).
> Conexión, `X13PATH` y `CEMENTO_PROXY` salen del bloque de env del **crontab** (cron no
> sourcea `.bashrc`; ver *Requisitos*).

### Fallas y código de salida

`python -m etl <dataset>` sale con **código 1** si la corrida registró alguna falla: fuente
inalcanzable, HTML/PDF/Excel que no parsea, o una serie que X-13 no pudo ajustar. El detalle
va por **stderr**; el log del dataset queda igual que siempre.

Lo que **no** es falla: un mes que la fuente todavía no publicó (`-> no publicado`) y un
`skipped` de X-13 por diseño (serie corta, huecos entre meses). Esas corridas salen 0.

> Hasta jul-2026 **todo** salía con código 0: una corrida con `leidos=0` porque la fuente
> estaba caída le reportaba éxito al cron. Se detectó con MAGyP caído (aves/bovinos) y con
> `granos/lino` congelada 10 días tras un timeout de X-13, sin que nadie se enterara.

### Control de ejecución: ¿el ETL está vivo?

Cada corrida deja una fila en **`etl_control_ejecucion`** (`etl/schema_control.sql`), ande o no
—y también si revienta con una excepción no capturada—: dataset, comando, inicio/fin, duración,
`estado` (`ok`/`falla`), el array de `fallas`, los contadores del `resumen [...]` y el
`ultimo_dato` del dataset después de correr.

**Por qué hace falta una tabla aparte**: las tablas de datos son append-only con
`insert_if_changed`, así que una corrida sin cambios **no escribe nada**. `max(ingested_at)` es
"último día que un valor cambió", no "último día que el ETL corrió": un ETL muerto hace tres
meses se ve idéntico a uno que corre a diario sin novedad. Si el **dato** está viejo se ve
leyendo las tablas (y la vista trae `ultimo_dato` en la misma fila); esta tabla dice si el
**proceso** está vivo.

Para consumir desde una app, dos vistas:

| Vista | Para qué |
|---|---|
| `etl_control_ultima` | última corrida de cada dataset que alguna vez corrió |
| **`etl_control_salud`** | los 15 datasets **siempre**, con dos veredictos: proceso y dato |

```sql
-- ¿Hay algo roto de nuestro lado? Si vuelve vacío, está todo bien.
select * from etl_control_salud where estado <> 'ok';

-- ¿Alguna fuente dejó de publicar? No es accionable, pero explica un dato que no avanza.
select dataset, ultimo_dato, dias_dato, dias_max_dato
from etl_control_salud where estado_dato <> 'ok';
```

`estado` mide el **proceso**: `ok` · `FALLA` (la última corrida falló) · `SIN_CORRER` (pasó más de
`horas_max` sin correr → el cron dejó de disparar) · `NUNCA_CORRIO` (sin ninguna fila). `horas_max`
es el hueco legítimo más largo según la ventana del cron: cemento corre los días 1-10, así que ~21
días sin correr es normal para él y no para `demanda_energia`, que corre diario.

`estado_dato` mide el **dato**: `ok` · `DATO_VIEJO` (`ultimo_dato` superó `dias_max_dato`) ·
`SIN_DATO` (dataset vacío). Van en columnas separadas porque son urgencias distintas: `estado`
es accionable por nosotros (hay algo que arreglar acá), `estado_dato` casi nunca lo es (el
organismo se atrasó). El caso que sí importa mirar es `estado = ok` con `estado_dato = DATO_VIEJO`
sostenido: puede ser que la fuente cambió de formato y el parser la esté ignorando sin fallar.
El detalle de cómo se derivan los umbrales está en `help_etl.md`.

> La escritura del control **nunca** rompe ni cambia el resultado de la corrida: si falla (base
> caída, tabla sin crear), avisa por stderr y sigue. Se crea con `python -m etl init-db`.

Para **recalcular la desestacionalización sin bajar de la web** (p.ej. después de cambiar el
cuadro `etl/series_desest.toml`), ver la sección *Recalcular la desest* más abajo (`redesest`).

## 4) Exportar los d11 (serie desestacionalizada) a CSV

```bash
python -m etl export                  # todos los datasets a CSV en la carpeta actual
python -m etl export automotriz       # solo automotriz -> automotriz_d11.csv
python -m etl export automotriz --dir ~/csvs
```
`automotriz_d11.csv`, `patentamientos_d11.csv` y `comex_d11.csv` salen en formato ancho (`date`
+ una columna por serie); granos y cemento en `date, d11`.

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
- `series_actual`: serie observada actual de granos + cemento + automotriz + patentamientos + acero + aves + leche + bovinos + demanda_energia + icc + icg + datos_gob.
- `series_desest`: serie desestacionalizada de los nueve.

Y para el **carril diario** (BCRA), una vista aparte que NO se mezcla con las mensuales:
- `series_diarias_actual`: serie observada de los datasets diarios (hoy solo `reservas_pasivos`). Misma
  forma que `series_actual` (`dataset, serie, date, valor, estado, fuente, ingested_at`) pero
  `date` es fecha diaria real.

> **Para consumir los datos** (mapa completo de tablas → vistas, series por dataset y columnas
> de cada vista), ver **[`INTEGRATION.md`](INTEGRATION.md)**.

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
| aves | `faena` | `mult` | `td` | `s3x5` |
| leche | `produccion` | `add` | `td1coef` | `s3x5` |
| bovinos | `produccion` | `add` | `td1coef` | `s3x5` |
| demanda_energia | `no_residencial` | `add` | `td1coef` | `s3x5` |

> **patentamientos** aún no tiene referencia de calibración: `mode=auto` deja que X-13 elija
> add/mult por AIC. La desest arranca en **2022-12** (`start` en el cuadro): el informe de
> nov-2022 es pago y falta, y X-13 exige meses contiguos.

> **acero** está calibrado contra la referencia (`Acero.xlsx`, columna `desest`): `add` +
> `td1coef` + `s3x5` reproduce el d11 con error ~0 (máx 0.001% sobre 401 meses). En `auto`
> X-13 converge al mismo modelo.

> **aves** es multiplicativa (`mult`). `mult` + `td` + `s3x5` es lo que **más se acerca** a la
> referencia (`Aves.xlsx`), pero **no la reproduce exacto**: ~0.26% de error medio (1.73% máx
> sobre 545 meses). La referencia se hizo con un método algo distinto del x13as del repo.

> **leche** está calibrada contra la referencia (`leche.xlsx`): `add` + `td1coef` + `s3x5`
> reproduce el d11 con error ~0 (máx 0.0002% sobre 137 meses). Los valores son **litros**
> (~9 dígitos): el núcleo envuelve las líneas del `.spc` por ancho de caracteres (`MAX_LINEA`)
> para que X-13 no parta un número — si no, una línea de 10 valores grandes desborda su límite.

> **bovinos**, como aves, **no reproduce exacto** la referencia (`Bovinos.xlsx`): `add` +
> `td1coef` + `s3x5` es lo que más se acerca (~0.29% medio, 3% máx sobre 341 meses). La
> referencia se hizo con un método algo distinto del x13as del repo.

> **demanda_energia** está calibrada contra la referencia (`energia.xlsx`, columna `desest`):
> `add` + `td1coef` + `s3x5` reproduce el d11 con error ~0 (medio 0.0000%, máx 0.0001% sobre
> 257 meses). La serie observada empalma `energia.xlsx` (2005→2022) con CAMMESA (2023→), contigua.
> Fuente en MWh → se guarda en GWh (÷1000); `no_residencial` = Demanda No Residencial + Demanda
> No Estacionalizada. Guión de calibración: `scripts/calibrar_demanda_energia.py`.

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

## Parsear meses: usar `etl/core/meses.py`

Las fuentes escriben los meses de todas las formas posibles y **septiembre es el problemático**:
`septiembre`, `setiembre`, `sept`, `sep` y `set`, las cinco vistas en documentos reales del repo.

Hasta agosto de 2026 cada dataset tenía su propio mapa —siete en total— y cada uno cubría un
conjunto distinto. No era duplicación inofensiva: se estaban rompiendo en lugares diferentes.

```
patentamientos:  "Sep.2026" OK   "Set.2026" OK   "Sept.2026" NO MATCHEA
utdt:            "sep-26"   OK   "set-26"   NO MATCHEA   "sept-26"  OK
granos:          sólo "SEPTIEMBRE" (sin "SETIEMBRE")
```

Y el modo de falla es el peor: **no explota, no matchea**. El mes no aparece, la serie se queda
quieta y nadie se entera hasta mirar el dato a mano.

```python
from etl.core import meses

meses.numero("Sept")       # 9   (tolera mayúsculas, acentos y punto final)
meses.numero("SETIEMBRE")  # 9
meses.anio_2d(26)          # 2026

# Para armar un regex, SIEMPRE con el helper y nunca con "|".join(...) a mano:
rx = re.compile(meses.alternancia(formas="cortas") + r"\.?\s*(\d{4}|\d{2})", re.IGNORECASE)
```

**Por qué `alternancia()` y no un `"|".join(...)`.** En un alternador de regex gana la primera
rama que matchea, no la más larga. Si `sep` va antes que `sept`, el texto "Sept.2026" matchea
`sep`, deja un `t` colgado y el regex falla entero. `alternancia()` ordena de más larga a más
corta, así que la regla queda escrita una sola vez y no se puede reintroducir el bug.

`formas` separa nombres completos (`largas`) de abreviaturas (`cortas`) porque hay parsers que
necesitan la distinción: `patentamientos` prueba primero el encabezado abreviado con año de dos
dígitos (`JUN.26`) y sólo después el título con nombre completo y año de cuatro.

> Migrados: `utdt` (icc/icg) y `patentamientos`. Faltan `acero`, `aves`, `cemento`, `granos` y
> `automotriz`, que tienen su mapa propio y hoy funcionan; conviene migrarlos al tocar cada ETL
> y verificando contra la base, no todos de una.

## La fuente de automotriz (ADEFA)

`etl/datasets/automotriz/source.py` baja el informe mensual y, con `pdfplumber`, lee las 3
cifras del mes (Producción Nacional / Exportaciones / Ventas a Concesionarios) de la tabla
**"Comparativo"**. Hay **dos canales con el mismo PDF**:

| Canal | URL | Rol |
|---|---|---|
| Estadísticas | `.../upload/estadisticas/resumen-<YYYY>-<MM>-es.pdf` | primario; el mes está en la ruta, un mes no publicado da 404 |
| Prensa | `.../es/prensa-archivo?id=<N>` | respaldo; ADEFA lo sube **antes**, adelanta el dato ~1 mes |

Los dos traen los **mismos números**: verificado 9 de 9 valores idénticos en abril, mayo y
junio de 2026. Por eso el respaldo entra con el mismo `estado='provisorio'` y, cuando
estadísticas publica el mes, el valor coincide y queda `sin_cambios`. La procedencia se
distingue en `fuente`, que guarda la URL usada. Se desactiva con `--no-prensa-fallback`.

**El `id` de la gacetilla es un contador, no un mes.** Entre el informe de octubre (288) y el
de noviembre (294) de 2025 hay cinco ids que son otra cosa, y varios devuelven
`application/octet-stream` sin ser PDF. Se descubre por `HEAD` leyendo el `Content-Disposition`
y matcheando el nombre del mes; nunca por aritmética. El `HEAD` importa: cada informe pesa
~3,7 MB y el barrido mira decenas de ids sin bajar un byte de cuerpo.

### La columna del mes se lee del encabezado, no por posición

El comparativo declara arriba el orden de sus columnas:

```
Jun 2026  Jul 2026  Var. %  Jul 2025  Var. %  Acumulado Acumulado Var. %
Producción Nacional  37.029  31.189  -15,8%  37.112  -16,0%  287.590  235.847  -18,0%
```

Antes se tomaba el 2º entero de la fila. Funcionaba **por accidente del layout**: si ADEFA saca
la columna del mes anterior, ese 2º entero pasa a ser `Jul 2025` — el mismo mes del año
anterior, un número plausible que se guardaría como si fuera del año en curso, sin excepción ni
aviso. Ahora se busca la columna rotulada con el (mes, año) pedido y, si no está, se corta con
`FormatoInesperado` en vez de adivinar. Es la doble guarda del canal de prensa: el nombre del
archivo tiene que nombrar el mes **y** el encabezado tiene que declararlo.

> Septiembre se abrevia **`Sept`** en ese encabezado, no `Sep` ni `Set`.

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

## La fuente de aves (MAGyP)

`etl/datasets/aves/source.py` scrapea la página de "Carne Aviar" de MAGyP
(`.../areas/aves/estadistica/carne/index.php`) y elige el xlsx **"Indicadores de Oferta y
Demanda"** de mayor año. Ese Excel trae la **faena mensual** (columna *Faena SENASA*, miles
de cabezas) de todos los años desde 2016 en un solo archivo, apilado por año (fila del año +
12 filas de mes). El `run` reingesta toda esa ventana y `insert_if_changed` absorbe las
revisiones de MAGyP; la cifra se guarda como `definitivo`.

El histórico profundo (faena 1981→) sale de `etl/datasets/aves/data/Aves.xlsx`
(`load-history`), cuya columna `desest` es la **referencia de calibración**. A diferencia de
acero, la desest **no reproduce exacto** esa referencia (mejor: `mult`+`td`+`s3x5`, ~0.26%
medio); ver la nota en *Desestacionalización*.

## La fuente de leche (MAGyP)

`etl/datasets/leche/source.py` baja el Excel **`PPV021_PPV022.xlsx`** de la Dirección Nacional
de Lechería (`.../ss_lecheria/estadisticas/_01_primaria/_archivos/`). La **URL es fija** —
MAGyP pisa el mismo archivo cada mes—, así que el `run` la baja directo (con un fallback que
scrapea la página por si algún día cambiara). Se lee la hoja *Mensual* (`MES | Litros`),
producción nacional en litros desde 2015. El Excel re-publica toda la serie, así que
`insert_if_changed` absorbe revisiones; se guarda como `definitivo`.

El histórico también está en `etl/datasets/leche/data/leche.xlsx` (`load-history`), cuya
columna `desest` es la **referencia de calibración** (reproducida con error ~0).

## La fuente de bovinos (MAGyP)

La página de bovinos no linkea el xls de datos directamente. `etl/datasets/bovinos/source.py`
sigue una cadena: scrapea la página de información sectorial → encuentra el PDF **"Tablero de
Faena Bovina"** → **extrae el hipervínculo embebido** dentro del PDF (con `pdfplumber`), que
apunta al xls mensual `Faena_Bovina_<años>_mensual..xls` → lo baja y parsea. El nombre del xls
cambia con el rango de años, pero el link del PDF siempre apunta al vigente.

El xls es formato `.xls` viejo (se lee con **`xlrd`**); se ubican por texto las columnas
*Mes/Año* y *Producción (miles tn res con hueso)* y se toma la producción (2019→, `definitivo`).
El histórico profundo (1998→) sale de `etl/datasets/bovinos/data/Bovinos.xlsx` (`load-history`),
cuya columna `desest` es la **referencia de calibración**.

### MAGyP publica DOS planillas de la misma serie, y no coinciden

De la misma página cuelgan dos PDFs, cada uno con su propio Excel embebido:

| PDF | Excel | Rango (ago-2026) |
|---|---|---|
| `Tablero_Faena_Bovino.pdf` | `Faena_Bovina_2019-2026_mensual.xls` | 2019-04 → **2026-07** |
| `Indicadores bovinos.pdf` (pág. 13) | `Planilla_indicadores_bovinos_desde_1990_MENSUAL.xlsx` | **1990-01** → 2026-06 |

El ETL diario usa el **primero** y así queda: es el que trae el mes más reciente, que es lo que
importa para el incremental. La planilla de 1990 se usó una sola vez para cargar los 8 años que
faltaban:

```bash
python -m etl bovinos load-history --magyp-1990   # one-off, ya aplicado: 96 meses (1990-1997)
```

**Los dos archivos se contradicen** en 4 de los 87 meses que comparten (peor caso 2019-06:
241,273 vs 234,634, un 2,75%). Por eso ese modo **sólo inserta meses anteriores al mínimo que ya
está en la base**: rellenar el solapamiento dejaría la serie flapeando, porque la corrida diaria
la volvería a pisar con los valores del Tablero al día siguiente. La regla lo vuelve además
idempotente por construcción — una segunda corrida no encuentra nada y no escribe.

Las dos planillas traen la producción en la **misma columna** (*Producción, en miles de toneladas
res con hueso*), así que el tramo 1990-1997 es homogéneo con el resto de la serie. Ojo al elegir
columna en la planilla de 1990: tiene 17 columnas y la única que dice *"toneladas equivalentes res
con hueso"* es la de **exportaciones**, otra serie — y no existe en el archivo del Tablero.

> La desestacionalizada **no** se recalculó al cargar 1990-1997: sigue arrancando en 1998.
> Extenderla cambiaría todos los valores desestacionalizados de la serie, y es una decisión aparte.

## La fuente de demanda_energia (CAMMESA)

`etl/datasets/demanda_energia/source.py` baja el Excel **"Demanda Mensual"** de CAMMESA por
URL fija (`download/demanda-mensual/?wpdmdl=41426`, el link de la página *estadística /
informe de síntesis del MEM*). Devuelve el `.xlsx` directo (no un zip), re-publicado entero
cada mes con ~1 mes de rezago. **Ojo:** existe otro link `demanda-mensual-2` (página
*gran-demanda*) que baja un zip pero **está atrasado** — no es el que se usa.

El xlsx tiene una sola hoja `DEMANDA` (unidad **MWh**), con una tabla **horizontal**: la fila
de encabezado (col A = `TIPO DEMANDA`) trae las fechas mensuales en las columnas, y debajo una
fila por tipo de demanda. Se parsea **por etiqueta de la col A**, no por número de fila —
CAMMESA agrega filas (p.ej. `MATE DISTRIBUIDOR`), que correrían un parseo posicional. Todas las
filas se guardan como series (en GWh, ÷1000), con `estado='definitivo'` (2023→). La serie
`no_residencial` (Demanda No Residencial + Demanda No Estacionalizada) es la principal; su
histórico profundo (2005→2022) sale de `etl/datasets/demanda_energia/data/energia.xlsx`
(`load-history`), cuya columna `desest` es la **referencia de calibración**.

## La fuente de comex (INDEC)

`etl/datasets/comex/source.py` baja **dos** planillas `.xls` del INDEC por URL fija
(`ftp/cuadros/economia/serie_mensual_indices_expo.xls` y `..._comex.xls`, los links *Series
mensuales* del cuadro **Índices de comercio exterior**). Las dos tienen el mismo layout y una
sola hoja: fila 2 el encabezado de bloque, fila 3 `Valor | Precio | Cantidad`, fila 5 en
adelante un mes por fila. Los bloques se ubican **leyendo el encabezado**, no por número de
columna: si INDEC agrega un rubro o los reordena, el parser lo sigue encontrando.

Traen **siempre el histórico completo** (2004-01 →), así que no hay `load-history`: la primera
corrida carga los 270 meses sola, por el camino de backfill masivo.

**Las dos planillas se pisan y eso es deliberado.** El bloque de exportaciones nivel general
está en las dos y es idéntico (verificado sobre los 270 meses: diferencia 0). Se ingesta el de
la planilla de expo —que además abre los rubros— y el de la de comex se parsea **sólo para
cruzarlo**. Si algún mes deja de coincidir, es que una de las dos cambió de base o de
metodología, y entonces el dato de importaciones —que sale sólo de la de comex— dejó de ser
comparable con el resto. El ETL lo reporta como falla (dispara el mail del cron) pero igual
ingesta lo que sí pudo leer.

> **El chequeo de base es un stop-the-world.** El título de la planilla declara `base 2004=100`
> y `source._check_base` corta la corrida si deja de decirlo. INDEC ya rebaseó una vez (venía de
> base 1993): apendear valores de una base nueva sobre la serie vieja dejaría un salto de nivel
> sin marcar, que es peor que no traer el dato.

`estado` sale de la planilla: INDEC marca con asterisco el **año** provisorio (`2026*`) y esa
marca aplica a sus 12 meses. Al 2026-08 son provisorios 2024, 2025 y 2026. Cuando cierra un año,
el asterisco desaparece y los mismos meses vuelven a entrar como snapshot `definitivo`; los dos
conviven y `etl_comex_actual` prioriza el definitivo. Por eso el incremental re-lee en cada
corrida **los meses de los años provisorios** (~30) más cualquier `(serie, mes, estado)` que no
esté cargado, y no toca los ya definitivos; `--full` los revisa igual.

> El criterio del incremental es por **estado**, no por fecha. Con un `date > último cargado`
> —lo natural— el pasaje provisorio → definitivo no entraría nunca: cuando INDEC cierra 2024,
> esos meses siguen siendo viejos y quedarían fuera de la ventana, con la serie marcada
> provisoria para siempre.
