# Integración / consumo de datos

Guía para **consumir** las series de este repo desde Postgres (BD `data` en `10.0.16.3`).

## Regla de oro: consumí las VISTAS, no las tablas

Cada tabla es **append-only**: una corrida inserta un snapshot nuevo (con `ingested_at`) por
cada valor nuevo o revisado, y **nunca pisa** un dato. Por eso la tabla cruda tiene varias
filas por `(serie, mes)`. Para consumir hay **dos vistas por dataset** que ya resuelven el
"último snapshot":

- **`<tabla>_actual`** — serie **observada**: el último snapshot por `(serie, mes)`, excluyendo
  la desestacionalizada. Si un mes tiene histórico (`estado NULL`) y dato mensual
  (`definitivo`/`provisorio`), gana el mensual.
- **`<tabla>_desest`** — serie **desestacionalizada** (X-13), un valor por `(serie, mes)`.

## Tablas y sus vistas

| Dataset (`python -m etl …`) | Tabla | Vista observada | Vista desestacionalizada |
|---|---|---|---|
| `granos` | `etl_molienda_granos` | `etl_molienda_granos_actual` | `etl_molienda_granos_desest` |
| `cemento` | `etl_cemento_despacho` | `etl_cemento_despacho_actual` | `etl_cemento_despacho_desest` |
| `automotriz` | `etl_automotriz` | `etl_automotriz_actual` | `etl_automotriz_desest` |
| `patentamientos` | `etl_patentamientos` | `etl_patentamientos_actual` | `etl_patentamientos_desest` |
| `acero` | `etl_acero` | `etl_acero_actual` | `etl_acero_desest` |
| `aves` | `etl_aves` | `etl_aves_actual` | `etl_aves_desest` |
| `leche` | `etl_leche` | `etl_leche_actual` | `etl_leche_desest` |
| `bovinos` | `etl_bovinos` | `etl_bovinos_actual` | `etl_bovinos_desest` |
| `demanda_energia` | `etl_demanda_energia` | `etl_demanda_energia_actual` | `etl_demanda_energia_desest` |

> Todas las tablas llevan prefijo **`etl_`**. El nombre de la tabla no siempre deriva directo
> del dataset (comando): `granos` → `etl_molienda_granos`, `cemento` → `etl_cemento_despacho`;
> el resto es `etl_<dataset>`. Consultá siempre las **vistas** `etl_<tabla>_actual` /
> `etl_<tabla>_desest` (o las unificadas), nunca la tabla append-only directamente.

## Vistas unificadas (recomendadas para consumo transversal)

Unen todos los datasets en una sola forma, agregando una columna `dataset`:

| Vista | Contenido |
|---|---|
| `series_actual` | serie **observada** de los 9 datasets (`dataset, serie, date, valor, estado, fuente, ingested_at`) |
| `series_desest` | serie **desestacionalizada** de los 9 (`dataset, serie, date, valor, fuente, ingested_at, parametros`) |

```sql
-- Ejemplo: última demanda no residencial desestacionalizada
select date, valor
from series_desest
where dataset = 'demanda_energia' and serie = 'no_residencial'
order by date;
```

## Series diarias (BCRA) — carril separado

Todo lo de arriba es **mensual**. El dataset `reservas_pasivos` (reservas internacionales y principales
pasivos del BCRA, archivo `diar_bas.xls`) es **diario** y vive en un carril aparte para NO mezclar
frecuencias: `date` es la **fecha diaria real** (no el primer día del mes) y **no** aparece en
`series_actual` / `series_desest`.

| Tabla (hechos) | Dimensión (nombres) | Vista observada | Vista unificada diaria |
|---|---|---|---|
| `etl_reservas_pasivos` | `etl_reservas_pasivos_series` | `etl_reservas_pasivos_actual` | `series_diarias_actual` |

- **`etl_reservas_pasivos`** es append-only, igual que las mensuales, pero `serie` es el **`cd_serie`** del
  BCRA (clave estable del archivo: `246`, `247`, … `8843`). No se desestacionaliza.
- **`etl_reservas_pasivos_series`** es la dimensión con los **nombres legibles**: `cd_serie` (PK), `codigo`
  (el `bas*` del Excel, solo referencia), `nombre`, `unidad` (`USD millones` / `ARS millones` /
  `tipo de cambio`), `grupo` (`reservas` / `pasivos` / `tipo_cambio`), `orden`.
- **`etl_reservas_pasivos_actual`** = último snapshot por `(serie, date)` **con JOIN al catálogo**, así ya
  trae `cd_serie, codigo, nombre, unidad, grupo, date, valor, estado, fuente, ingested_at`.
- **`series_diarias_actual`** une los datasets diarios (hoy solo `reservas_pasivos`) en la forma
  `dataset, serie, date, valor, estado, fuente, ingested_at`.

Son 37 series: reservas internacionales (total + componentes, en USD), principales pasivos (base
monetaria, circulación, cuenta corriente, LEBAC/LELIQ/NOCOM, pases, depósitos del gobierno, etc.,
en pesos o USD según la serie) y los tipos de cambio de valuación y referencia.

```sql
-- Últimas reservas internacionales totales (con nombre y unidad)
select nombre, unidad, date, valor
from etl_reservas_pasivos_actual
where cd_serie = '246'
order by date desc
limit 5;

-- Todas las series de un día, legibles
select nombre, grupo, unidad, valor
from etl_reservas_pasivos_actual
where date = (select max(date) from etl_reservas_pasivos_actual)
order by orden;
```

> Regla igual que en las mensuales: consumí las **vistas** (`etl_reservas_pasivos_actual` /
> `series_diarias_actual`), nunca `etl_reservas_pasivos` cruda (append-only, varias filas por `serie/día`).

## Columnas de las vistas

**`<tabla>_actual` / `series_actual`**

| Columna | Significado |
|---|---|
| `dataset` | *(solo en `series_actual`)* nombre del dataset |
| `serie` | serie dentro del dataset (ver tabla siguiente) |
| `date` | primer día del mes |
| `valor` | valor observado (unidad según dataset) |
| `estado` | `NULL` = histórico (Excel) · `provisorio`/`definitivo` = fuente mensual |
| `fuente` | origen del dato (Excel histórico / URL de la fuente) |
| `ingested_at` | timestamp del snapshot |

**`<tabla>_desest` / `series_desest`**

| Columna | Significado |
|---|---|
| `dataset` | *(solo en `series_desest`)* nombre del dataset |
| `serie` | serie desestacionalizada |
| `date` | primer día del mes |
| `valor` | valor desestacionalizado (d11 de X-13) |
| `fuente` | `census x13` |
| `ingested_at` | timestamp del cálculo |
| `parametros` | jsonb con la config X-13 usada (modo, trading-day, filtro, arima, etc.) |

## Series por dataset

`obs` = presentes en la vista `_actual`; `desest` = presentes en la vista `_desest`.

| Dataset | Series observadas | Series desestacionalizadas |
|---|---|---|
| `granos` | `total`, `soja`, `girasol`, `lino`, `mani`, `algodon`, `cartamo`, `canola` | `total`, `soja`, `girasol`, `mani` |
| `cemento` | `despacho_nacional`, `exportacion`, `consumo_despacho_nacional`, `importaciones_propias` | `despacho_nacional` |
| `automotriz` | `produccion`, `ventas`, `expo` | `produccion`, `ventas`, `expo` |
| `patentamientos` | `total_mercado`, `autos`, `comercial_liviano`, `comercial_pesado`, `otros_pesados`, `autos_cl`, `autos_cl_cp` | *(las 7)* |
| `acero` | `acero_crudo` | `acero_crudo` |
| `aves` | `faena` | `faena` |
| `leche` | `produccion` | `produccion` |
| `bovinos` | `produccion` | `produccion` |
| `demanda_energia` | `estacionalizada`, `residencial`, `no_res_estacionalizada`, `no_estacionalizada`, `gudi`, `gume`, `guma`, `mate_distribuidor`, `local`, `no_residencial` | `no_residencial` |

> Las series que **no** están en `_desest` (p.ej. `lino`/`algodon`/`cartamo`/`canola` de granos, o
> las componentes de `demanda_energia`) quedan solo como observadas: X-13 no las ajusta (molienda
> intermitente, o son insumos de una serie derivada). Ver `etl/series_desest.toml`.

## ¿El ETL está vivo? (`etl_control_salud`)

Las tablas de arriba dicen **hasta dónde llega el dato**. No dicen si el ETL sigue corriendo:
son append-only y una corrida sin cambios no escribe nada, así que `max(ingested_at)` significa
"último día que un valor cambió", no "último día que el ETL corrió".

Para eso está **`etl_control_ejecucion`**, con una fila por corrida (ande o no), y su vista:

```sql
-- Chequeo rápido desde la app: si vuelve vacío, está todo bien.
select * from etl_control_salud where estado <> 'ok';
```

| Columna | Significado |
|---|---|
| `dataset` | los 10, hayan corrido o no |
| `estado` | `ok` · `FALLA` · `SIN_CORRER` · `NUNCA_CORRIO` |
| `estado_ultima_corrida` | `ok` / `falla` de la última ejecución |
| `ultima_corrida` · `horas_desde` | cuándo terminó y hace cuánto |
| `horas_max` | hueco legítimo máximo según la ventana del cron |
| `ultimo_dato` | `max(date)` del dataset tras esa corrida |
| `fallas` | array con el detalle; `NULL` si anduvo |

`SIN_CORRER` significa que el cron dejó de disparar. `FALLA` significa que corrió y no pudo
traer el dato: ahí sí conviene ir al log, `/home/jmt/data/etls/<dataset>.log`.
