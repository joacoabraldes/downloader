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
| `icc` | `etl_icc` | `etl_icc_actual` | `etl_icc_desest` (vacía: no se desestacionaliza) |
| `icg` | `etl_icg` | `etl_icg_actual` | `etl_icg_desest` (vacía: no se desestacionaliza) |
| `compras_granos` | `etl_compras_granos` | `etl_compras_granos_actual` | — (semanal, no se desestacionaliza) |

> Todas las tablas llevan prefijo **`etl_`**. El nombre de la tabla no siempre deriva directo
> del dataset (comando): `granos` → `etl_molienda_granos`, `cemento` → `etl_cemento_despacho`;
> el resto es `etl_<dataset>`. Consultá siempre las **vistas** `etl_<tabla>_actual` /
> `etl_<tabla>_desest` (o las unificadas), nunca la tabla append-only directamente.

## Vistas unificadas (recomendadas para consumo transversal)

Unen todos los datasets en una sola forma, agregando una columna `dataset`:

| Vista | Contenido |
|---|---|
| `series_actual` | serie **observada** de los 11 datasets mensuales (`dataset, serie, date, valor, estado, fuente, ingested_at`) |
| `series_desest` | serie **desestacionalizada** (`dataset, serie, date, valor, fuente, ingested_at, parametros`). `icc` e `icg` no aportan filas: se publican sin ajuste estacional |

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

## Series semanales (MAGyP) — carril separado

`compras_granos` son las **compras de granos del sector exportador y de la industria, más las
DJVE** (declaraciones juradas de venta al exterior), tal como las publica MAGyP cada semana. Es
**semanal** y vive en un carril aparte: `date` es la **fecha de corte del informe** y no aparece
en `series_actual` / `series_diarias_actual`.

| Tabla (hechos) | Vista observada | Cobertura |
|---|---|---|
| `etl_compras_granos` | `etl_compras_granos_actual` | **1.109 semanas, 2005-03-02 → 2026-07-22** |

### Cómo se identifica una fila

No hay columna `serie`: la fila se identifica por **cuatro dimensiones** más la fecha.

| Columna | Valores |
|---|---|
| `cultivo` | `trigo`, `maiz`, `sorgo`, `cebada_cervecera`, `cebada_forrajera`, `soja`, `girasol` |
| `cosecha` | campaña comercial tal como la publica la fuente: `25/26`, `24/25`, … |
| `sector` | `exportador`, `industria`, `total` |
| `metrica` | ver la tabla de abajo |
| `date` | fecha de corte del informe semanal |
| `valor` | **miles de toneladas** |
| `corte` | fecha de corte **propia del bloque** cuando difiere de `date` |

Qué significa cada métrica:

| Métrica | Qué es |
|---|---|
| `semanal` | compras de esa semana |
| `total_comprado` | compras acumuladas de la campaña, bajo cualquier modalidad |
| `precio_hecho` | del acumulado, lo que ya tiene precio pactado |
| `a_fijar` | del acumulado, lo comprometido sin precio pactado |
| `fijado` | de lo "a fijar", lo que ya fijó precio |
| `saldo_a_fijar` | `a_fijar` − `fijado` |
| `djve_acum` | DJVE acumuladas de la campaña |
| `embarque_estimado` | embarque estimado acumulado del año comercial (**formato viejo**; NO es lo mismo que DJVE) |
| `compras_estimadas` / `compras_declaradas` | bloque industria del formato viejo |
| `ventas_potenciales` / `ventas_efectivas` | primeros años del formato viejo |

### Las tres trampas (leer antes de escribir la primera query)

**1. Filtrá SIEMPRE por `cosecha`.** En cada semana conviven **2 o 3 campañas por cultivo**: la que
está terminando, la que está en curso y la que se empieza a vender por adelantado. Al 22/07/2026,
maíz tiene tres:

| cosecha | `total_comprado` (sector `total`) |
|---|---|
| `26/27` | 823,4 |
| `25/26` | 33.875,7 |
| `24/25` | 36.972,3 |

Filtrar por cultivo sin filtrar campaña te da **series superpuestas**, no una serie.

**2. `sector = 'total'` ya es la suma.** `total` = `exportador` + `industria`, y lo publica la
propia fuente. Si sumás los tres, contás todo dos veces.

**3. `corte` ≠ `date` en el bloque industria.** El bloque de industria suele venir **atrasado**
respecto del encabezado del informe: en el informe del 01/07/2026 la industria está "AL
27/05/2026". Pasa en **941 de las 1.109 semanas** (~28.900 filas, casi todas de `industria`). Si
comparás semana contra semana sin mirar `corte`, la industria parece repetir valores sin motivo —
y no es un bug, es que el dato no se actualizó. Cuando el bloque no declara fecha propia,
`corte` = `date`.

### Qué métricas hay en cada época

La fuente cambió de formato dos veces, así que **no todas las métricas existen en todas las
fechas**. Rangos reales, medidos sobre la base cargada:

| Métrica | Desde | Hasta |
|---|---|---|
| `semanal`, `total_comprado`, `a_fijar`, `fijado` | 2005-03-02 | vigente |
| `ventas_potenciales` | 2005-03-02 | 2007-03-07 |
| `ventas_efectivas` | 2005-03-02 | 2008-10-15 |
| `compras_estimadas`, `compras_declaradas` | 2005-03-02 | 2017-04-26 |
| `embarque_estimado` | 2005-03-02 | 2017-11-08 |
| `djve_acum` | 2017-11-15 | vigente |
| `precio_hecho`, `saldo_a_fijar` | 2019-04-03 | vigente |

> El cambio de formato es **limpio y datable**: `embarque_estimado` muere el **2017-11-08** y
> `djve_acum` nace el **2017-11-15**, la semana siguiente. No hay solapamiento, así que **no
> intentes empalmarlas**: miden cosas distintas (embarques efectivos vs. declaraciones de venta).
>
> La fuente sigue mostrando el encabezado "VENTAS" hasta 2015, pero con "SIN DATOS" debajo: por
> eso los valores de `ventas_*` terminan mucho antes de lo que sugiere el HTML.

También hay **un hueco legítimo** en la serie: entre el **2013-12-18 y el 2014-01-03** no hay
semana, porque la fuente declaró que la del 26/12/2013 se acumuló en la del 03/01/2014.

### Queries

Todas probadas contra la base.

```sql
-- Foto de la última semana publicada: soja, campaña 25/26, por sector
select sector, metrica, valor, corte
from etl_compras_granos_actual
where cultivo = 'soja' and cosecha = '25/26'
  and date = (select max(date) from etl_compras_granos_actual)
order by sector, metrica;

-- Qué campañas están activas hoy y con cuánto volumen (para elegir cuál graficar)
select cultivo, cosecha, valor as total_comprado
from etl_compras_granos_actual
where metrica = 'total_comprado' and sector = 'total'
  and date = (select max(date) from etl_compras_granos_actual)
order by cultivo, cosecha desc;

-- Serie semanal para graficar: DJVE acumulada de trigo, campaña 25/26
select date, valor
from etl_compras_granos_actual
where cultivo = 'trigo' and cosecha = '25/26'
  and sector = 'exportador' and metrica = 'djve_acum'
order by date;

-- Ritmo de compra: semana a semana + media móvil de 4 semanas
select date, valor,
       round(avg(valor) over (order by date rows between 3 preceding and current row)::numeric, 1) as mm4
from etl_compras_granos_actual
where cultivo = 'maiz' and cosecha = '25/26'
  and sector = 'exportador' and metrica = 'semanal'
order by date;
```

**Comparar campañas entre sí** merece su propia nota. No uses `extract(week from date)`: una
campaña abarca **dos años calendario**, así que una misma semana del año aparece dos veces dentro
de la misma campaña y te duplica las filas. Alineá cada campaña por **su propia semana**:

```sql
-- Compras acumuladas de soja en las primeras 6 semanas de cada campaña, comparables
with base as (
  select cosecha, date, valor,
         dense_rank() over (partition by cosecha order by date) as semana_campania
  from etl_compras_granos_actual
  where cultivo = 'soja' and sector = 'total' and metrica = 'total_comprado'
)
select semana_campania,
       max(valor) filter (where cosecha = '25/26') as c_25_26,
       max(valor) filter (where cosecha = '24/25') as c_24_25,
       max(valor) filter (where cosecha = '23/24') as c_23_24
from base
where semana_campania <= 6
group by 1 order by 1;
```

### Frescura

```sql
select max(date) as ultima_semana, current_date - max(date) as dias_desde
from etl_compras_granos_actual;
```

La fuente publica con rezago: es normal ver **7 a 14 días** desde la última semana cargada. Para
saber si el problema es el ETL o la fuente, mirá `etl_control_salud` (sección de más abajo): si el
ETL corrió `ok` y `ultimo_dato` no se movió, es que MAGyP todavía no publicó.

> Misma regla que en el resto: consumí `etl_compras_granos_actual`, nunca `etl_compras_granos`
> cruda (es append-only y tiene varias filas por clave).

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
| `icc` | `nacional`, `capital`, `gba`, `interior`, `situacion_personal`, `situacion_macro`, `bienes_durables` | *(ninguna)* |
| `icg` | `icg` | *(ninguna)* |

> Las series que **no** están en `_desest` (p.ej. `lino`/`algodon`/`cartamo`/`canola` de granos, o
> las componentes de `demanda_energia`) quedan solo como observadas: X-13 no las ajusta (molienda
> intermitente, o son insumos de una serie derivada). Ver `etl/series_desest.toml`.
>
> `icc` e `icg` no tienen **ninguna** serie desestacionalizada, y es a propósito: UTDT los publica
> crudos, así que no existe una referencia contra la cual calibrar el ajuste. Sus vistas `_desest`
> existen y devuelven 0 filas.

**Los dos datasets fuera de este cuadro** no tienen columna `serie` con la forma de arriba:
`reservas_pasivos` usa el `cd_serie` del BCRA (ver *Series diarias*) y `compras_granos` se
identifica por cuatro dimensiones (`cultivo`, `cosecha`, `sector`, `metrica`; ver *Series
semanales*). Ninguno de los dos se desestacionaliza.

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
| `dataset` | los 13, hayan corrido o no |
| `estado` | `ok` · `FALLA` · `SIN_CORRER` · `NUNCA_CORRIO` |
| `estado_ultima_corrida` | `ok` / `falla` de la última ejecución |
| `ultima_corrida` · `horas_desde` | cuándo terminó y hace cuánto |
| `horas_max` | hueco legítimo máximo según la ventana del cron |
| `ultimo_dato` | `max(date)` del dataset tras esa corrida |
| `fallas` | array con el detalle; `NULL` si anduvo |

`SIN_CORRER` significa que el cron dejó de disparar. `FALLA` significa que corrió y no pudo
traer el dato: ahí sí conviene ir al log, `/home/jmt/data/etls/<dataset>.log`.

## ICC e ICG (UTDT) — cómo consumirlos

Los dos índices de confianza de UTDT se consumen como cualquier otro dataset mensual, pero
tienen tres particularidades que conviene tener a mano.

```sql
-- ICC: las 7 series
select serie, date, valor from etl_icc_actual order by serie, date;

-- ICG: la única serie
select date, valor from etl_icg_actual order by date;

-- Transversal, junto al resto de los datasets
select serie, date, valor from series_actual where dataset = 'icc';
```

| Dataset | Series | Escala | Desde |
|---|---|---|---|
| `icc` | `nacional`, `capital`, `gba`, `interior`, `situacion_personal`, `situacion_macro`, `bienes_durables` | **0-100** | `capital` 1998-07; las otras 6, 2001-03 |
| `icg` | `icg` | **0-5** | 2001-11 |

**1. Las escalas son distintas.** El ICC va de 0 a 100 y el ICG de 0 a 5. No van en el mismo eje.

**2. La variación mensual no está guardada**, y es a propósito: se deriva del nivel, y
guardarla sería duplicar un estado que se puede desincronizar.

```sql
select date, valor,
       valor / lag(valor) over (order by date) - 1 as var_mensual
from etl_icc_actual where serie = 'nacional' order by date;
```

Eso reproduce el titular que publica UTDT (julio-2026: -4,8%).

**3. No hay serie desestacionalizada.** `series_desest` no devuelve filas para `icc` ni `icg`:
UTDT los publica crudos y no existe una referencia contra la cual calibrar X-13, así que un
ajuste propio sería un número inventado y no "el ICC desestacionalizado". Las vistas
`etl_icc_desest` / `etl_icg_desest` existen y devuelven 0 filas (ver `etl/series_desest.toml`).

Como en el resto del repo, `date` es el primer día del mes y hay que consultar **las vistas**,
nunca las tablas `etl_icc` / `etl_icg`: son append-only y guardan un snapshot por revisión.

> Al graficar el ICC, ojo con el arranque desparejo: `capital` tiene 337 meses y las otras seis
> 305. Un `join` por `date` sin cuidado se come el tramo 1998-2001.


