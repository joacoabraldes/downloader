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
| `granos` | `molienda_granos` | `molienda_granos_actual` | `molienda_granos_desest` |
| `cemento` | `cemento_despacho` | `cemento_despacho_actual` | `cemento_despacho_desest` |
| `automotriz` | `automotriz` | `automotriz_actual` | `automotriz_desest` |
| `patentamientos` | `patentamientos` | `patentamientos_actual` | `patentamientos_desest` |
| `acero` | `acero` | `acero_actual` | `acero_desest` |
| `aves` | `aves` | `aves_actual` | `aves_desest` |
| `leche` | `etl_leche` | `etl_leche_actual` | `etl_leche_desest` |
| `bovinos` | `bovinos` | `bovinos_actual` | `bovinos_desest` |
| `demanda_energia` | `etl_demanda_energia` | `etl_demanda_energia_actual` | `etl_demanda_energia_desest` |

> El **nombre del dataset** (comando) no siempre coincide con el de la tabla: `granos` →
> `molienda_granos`, `cemento` → `cemento_despacho`, `leche` → `etl_leche`,
> `demanda_energia` → `etl_demanda_energia`. El resto coincide.

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
| `granos` | `total`, `soja`, `girasol`, `lino`, `mani`, `algodon`, `cartamo`, `canola` | `total`, `soja`, `girasol`, `lino`, `mani` |
| `cemento` | `despacho_nacional`, `exportacion`, `consumo_despacho_nacional`, `importaciones_propias` | `despacho_nacional` |
| `automotriz` | `produccion`, `ventas`, `expo` | `produccion`, `ventas`, `expo` |
| `patentamientos` | `total_mercado`, `autos`, `comercial_liviano`, `comercial_pesado`, `otros_pesados`, `autos_cl`, `autos_cl_cp` | *(las 7)* |
| `acero` | `acero_crudo` | `acero_crudo` |
| `aves` | `faena` | `faena` |
| `leche` | `produccion` | `produccion` |
| `bovinos` | `produccion` | `produccion` |
| `demanda_energia` | `estacionalizada`, `residencial`, `no_res_estacionalizada`, `no_estacionalizada`, `gudi`, `gume`, `guma`, `mate_distribuidor`, `local`, `no_residencial` | `no_residencial` |

> Las series que **no** están en `_desest` (p.ej. `algodon`/`cartamo`/`canola` de granos, o las
> componentes de `demanda_energia`) quedan solo como observadas: X-13 no las ajusta (molienda
> intermitente, o son insumos de una serie derivada). Ver `etl/series_desest.toml`.
