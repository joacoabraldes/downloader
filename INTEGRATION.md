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
| `transferencias` | `etl_transferencias` | `etl_transferencias_actual` | `etl_transferencias_desest` |
| `acero` | `etl_acero` | `etl_acero_actual` | `etl_acero_desest` |
| `aves` | `etl_aves` | `etl_aves_actual` | `etl_aves_desest` |
| `leche` | `etl_leche` | `etl_leche_actual` | `etl_leche_desest` |
| `bovinos` | `etl_bovinos` | `etl_bovinos_actual` | `etl_bovinos_desest` |
| `demanda_energia` | `etl_demanda_energia` | `etl_demanda_energia_actual` | `etl_demanda_energia_desest` |
| `hidrocarburos` | `etl_hidrocarburos` | `etl_hidrocarburos_actual` | `etl_hidrocarburos_desest` (sólo los 2 totales) |
| `refinacion` | `etl_refinacion` + dimensión `etl_refinacion_series` | `etl_refinacion_actual` (+ `_conceptos` y `_totales`) | `etl_refinacion_desest` (vacía: no se desestacionaliza) |
| `escrituras_caba` | `etl_escrituras_caba` | `etl_escrituras_caba_actual` | `etl_escrituras_caba_desest` (sólo `compraventa`) |
| `icc` | `etl_icc` | `etl_icc_actual` | `etl_icc_desest` (vacía: no se desestacionaliza) |
| `icg` | `etl_icg` | `etl_icg_actual` | `etl_icg_desest` (vacía: no se desestacionaliza) |
| `datos_gob` | `etl_datos_gob` + dimensión `etl_datos_gob_series` | `etl_datos_gob_actual` (+ `_real` y `_completo`) | `etl_datos_gob_desest` (las 2 de ventas + las 2 de comercio exterior) |
| `comex` | `etl_comex` + dimensión `etl_comex_series` | `etl_comex_actual` | `etl_comex_desest` (sólo las 6 de cantidad) |
| `compras_granos` | `etl_compras_granos` | `etl_compras_granos_actual` | — (semanal, no se desestacionaliza) |
| `fob_granos` | `etl_fob_granos` | `etl_fob_granos_diario` / `etl_fob_granos_mensual` | — (diario, no se desestacionaliza) |
| `cot` | `etl_cot` | `etl_cot_actual` / `etl_cot_neto` | — (semanal, no se desestacionaliza) |

> Todas las tablas llevan prefijo **`etl_`**. El nombre de la tabla no siempre deriva directo
> del dataset (comando): `granos` → `etl_molienda_granos`, `cemento` → `etl_cemento_despacho`;
> el resto es `etl_<dataset>`. Consultá siempre las **vistas** `etl_<tabla>_actual` /
> `etl_<tabla>_desest` (o las unificadas), nunca la tabla append-only directamente.

## Vistas unificadas (recomendadas para consumo transversal)

Unen todos los datasets en una sola forma, agregando una columna `dataset`:

| Vista | Contenido |
|---|---|
| `series_actual` | serie **observada** de los 17 datasets mensuales (`dataset, serie, date, valor, estado, fuente, ingested_at`) |
| `series_desest` | serie **desestacionalizada** (`dataset, serie, date, valor, fuente, ingested_at, parametros`). `icc` e `icg` no aportan filas: se publican sin ajuste estacional. De `datos_gob` se ajustan las 2 de ventas y las 2 de comercio exterior (sobre su serie real); de `comex`, sólo las seis de cantidad |

```sql
-- Ejemplo: última demanda no residencial desestacionalizada
select date, valor
from series_desest
where dataset = 'demanda_energia' and serie = 'no_residencial'
order by date;
```

## Series diarias — carril separado

Todo lo de arriba es **mensual**. Dos datasets son **diarios** y viven en un carril aparte para NO
mezclar frecuencias: `date` es la **fecha diaria real** (no el primer día del mes) y **no** aparecen
en `series_actual` / `series_desest`.

| Dataset | Tabla (hechos) | Dimensión / vista de día | Vista observada | Vista unificada diaria |
|---|---|---|---|---|
| `reservas_pasivos` | `etl_reservas_pasivos` | `etl_reservas_pasivos_series` | `etl_reservas_pasivos_actual` | `series_diarias_actual` |
| `fob_granos` | `etl_fob_granos` | `etl_fob_granos_diario` | `etl_fob_granos_actual` | `series_diarias_actual` |

`fob_granos` tiene su propia sección más abajo (**`fob_granos` y el PRP de granos**): entra al
carril diario por `etl_fob_granos_diario`, no por su `*_actual`, porque la tabla de hechos guarda
**varias filas por (producto, día)** —una por ventana de embarque de la curva forward— y la
unificada necesita una sola serie por día.

### `reservas_pasivos` (BCRA)

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

## Series semanales — carril separado

Dos datasets son **semanales** y viven en un carril aparte: `date` es la **fecha de corte del
informe** y no aparecen en `series_actual` / `series_diarias_actual`.

| Dataset | Qué es | Vista de consumo |
|---|---|---|
| `compras_granos` | compras del sector exportador y de la industria, más DJVE (MAGyP) | `etl_compras_granos_actual` |
| `cot` | posición especulativa en soja, maíz y trigo SRW (CFTC) | `etl_cot_neto` |

`cot` tiene su propia sección más abajo (**`cot` (Commitments of Traders, CFTC)**).

### `compras_granos` (MAGyP)

Son las **compras de granos del sector exportador y de la industria, más las DJVE**
(declaraciones juradas de venta al exterior), tal como las publica MAGyP cada semana.

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
| `estado` | `NULL` = histórico (Excel) · `provisorio`/`definitivo` = fuente mensual. **No todos los datasets usan los tres**: `transferencias` es siempre `provisorio` porque la DNRPA nunca cierra un mes, y `escrituras_caba` agrega `relleno`. Si filtrás por `estado = 'definitivo'` vas a perder datasets enteros — filtrá por lo que **no** querés (`estado is distinct from 'desestacionalizado'`) o usá la vista `_actual`, que ya resuelve la prioridad |
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
| `transferencias` | `autos` | `autos` |
| `acero` | `acero_crudo` | `acero_crudo` |
| `aves` | `faena` | `faena` |
| `leche` | `produccion` | `produccion` |
| `bovinos` | `produccion` | `produccion` |
| `demanda_energia` | `estacionalizada`, `residencial`, `no_res_estacionalizada`, `no_estacionalizada`, `gudi`, `gume`, `guma`, `mate_distribuidor`, `local`, `no_residencial` | `no_residencial` |
| `hidrocarburos` | `petroleo`, `gas` (totales) + `<serie>_convencional`, `_shale`, `_tight` | `petroleo`, `gas` *(sólo los totales)* |
| `escrituras_caba` | `compraventa`, `monto`, `hipotecas`, `monto_medio`, `monto_medio_usd` | `compraventa` |
| `icc` | `nacional`, `capital`, `gba`, `interior`, `situacion_personal`, `situacion_macro`, `bienes_durables` | *(ninguna)* |
| `icg` | `icg` | *(ninguna)* |
| `datos_gob` | las 14: `isac`, `ipi_manufacturero`, `ipc_nacional`, `expo_total`, `impo_total`, `ventas_supermercados`, `ventas_centros_compras`, `ripte`, `smvm`, `indice_salarios_total`, `indice_salarios_registrado`, `indice_salarios_priv_registrado`, `indice_salarios_publico`, `indice_salarios_priv_no_registrado` | `ventas_supermercados`, `ventas_centros_compras`, `expo_total`, `impo_total` *(las 4 sobre la serie real)* + `isac`, `ipi_manufacturero` *(oficiales de INDEC, no X-13)* |
| `comex` | las 18: `expo_{valor,precio,cantidad}_{general,primarios,moa,moi,combustibles}` + `impo_{valor,precio,cantidad}_general` | las 6 de cantidad: `expo_cantidad_{general,primarios,moa,moi,combustibles}`, `impo_cantidad_general` |

> Las series que **no** están en `_desest` (p.ej. `lino`/`algodon`/`cartamo`/`canola` de granos, o
> las componentes de `demanda_energia`) quedan solo como observadas: X-13 no las ajusta (molienda
> intermitente, o son insumos de una serie derivada). Ver `etl/series_desest.toml`.
>
> **`escrituras_caba` mezcla unidades entre sus series y NO se suman entre sí.** `compraventa` e
> `hipotecas` son conteos de actos (y `hipotecas` es un **subconjunto** de `compraventa`, no algo
> aparte que se sume); `monto` y `monto_medio` están en **pesos corrientes**; `monto_medio_usd` en
> **dólares**. Se cumple `monto / compraventa = monto_medio` con 0,0009% de error mediano, así que
> sirve de control cruzado. Las dos series en pesos son **nominales**: bajo inflación argentina no
> se comparan mes contra mes sin deflactar. Sólo `compraventa` tiene cobertura completa (126
> meses); las otras cuatro tienen huecos porque dependen de cómo estaba redactado cada informe.
> **Sólo `compraventa` se desestacionaliza**, y conviene usarla: enero cae ~55% contra diciembre
> todos los años por calendario puro, así que el m/m del crudo es engañoso. Las otras cuatro no
> se ajustan (huecos, o pesos nominales).
>
> **`hidrocarburos` mezcla unidades y niveles**: `petroleo*` está en **miles de m3** y `gas*` en
> **millones de m3**, así que no se suman entre sí. Además conviven el total y su desagregado por
> tipo de recurso: `sum(valor)` sobre todo el dataset **cuenta doble**. Para el total filtrá
> `serie in ('petroleo','gas')`. Los totales arrancan en 1996-01 y el desagregado en 2009-01.
>
> **Los tres datasets de autos NO se solapan y se pueden sumar o mirar juntos sin miedo:**
> `automotriz` es producción/ventas/exportación de **fábrica** (ADEFA), `patentamientos` son
> registraciones de **0km** (SIOMAA) y `transferencias` es el mercado de **usados** — cambio de
> titular de un vehículo que ya estaba en el parque (DNRPA). Ojo con la unidad de `automotriz`:
> sus `ventas` son mayoristas (fábrica a concesionario), no ventas al público. `transferencias`
> es la única de las tres con histórico desde 1995.
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
| `dataset` | los 20, hayan corrido o no |
| `estado` | **proceso**: `ok` · `FALLA` · `SIN_CORRER` · `NUNCA_CORRIO` |
| `estado_ultima_corrida` | `ok` / `falla` de la última ejecución |
| `ultima_corrida` · `horas_desde` | cuándo terminó y hace cuánto |
| `horas_max` | hueco legítimo máximo según la ventana del cron |
| `ultimo_dato` | `max(date)` del dataset tras esa corrida |
| `fallas` | array con el detalle; `NULL` si anduvo |
| `dias_dato` · `dias_max_dato` | edad de `ultimo_dato` y su máximo tolerado |
| `estado_dato` | **dato**: `ok` · `DATO_VIEJO` · `SIN_DATO` |

`SIN_CORRER` significa que el cron dejó de disparar. `FALLA` significa que corrió y no pudo
traer el dato.

### Leer los errores que reportó el ETL

**No hace falta ir al log.** El detalle de cada falla queda en la base, en la columna `fallas`
(`text[]`) de `etl_control_ejecucion`, y la propaga `etl_control_ultima` y `etl_control_salud`.
El log (`/home/jmt/data/etls/<dataset>.log`) sólo agrega el ruido de alrededor.

```sql
-- Una fila por error, listo para mostrar o alertar.
select dataset, estado, ultima_corrida, falla
from etl_control_salud, unnest(fallas) as falla
where fallas is not null
order by ultima_corrida desc;
```

Cada string tiene el formato **`<dataset> / <comando>[ <ítem>]: <mensaje>`**, donde `<ítem>` es la
serie o el mes que falló, cuando la falla es de uno solo:

```
leche / run: bajando/parseando: HTTPSConnectionPool(host='www.magyp.gob.ar', port=443): ...
hidrocarburos / run: petroleo: 503 Server Error: Service Unavailable for url: https://...
hidrocarburos / run: gas: 503 Server Error: Service Unavailable for url: https://...
datos_gob / run isac: ERROR bajando 33.2_ISAC_NIVELRAL_0_M_18_63: ...
datos_gob / desest expo_total: <motivo por el que X-13 no pudo ajustar>
```

**Cuatro cosas que hay que saber antes de construir un alerta encima:**

**1. `fallas` es un array, no un texto.** Una corrida puede fallar en varias series a la vez —
`hidrocarburos` arriba trae dos. Concatenarlo con `array_to_string` para mostrarlo está bien;
contarlo como un solo error, no.

**2. `estado = 'falla'` NO significa que no entró nada.** Cada dataset baja varias series y la
falla es de la corrida, no del dataset entero. Si `datos_gob` falla bajando `isac`, las otras 13
series entraron igual y son datos buenos. Para saber cuánto entró, mirá los contadores:

```sql
select dataset, estado, leidos, nuevos, actualizados, fallas
from etl_control_ultima where estado = 'falla';
```

> `etl_control_ultima` expone `leidos`, `nuevos` y `actualizados`. El juego completo de contadores
> (`sin_cambios`, `saltados`, `no_publicado`, los de X-13) está en `etl_control_ejecucion`.

**3. Las tablas de datos NO llevan marca de error.** Ninguna fila de `etl_datos_gob` dice "esta
corrida falló". La única forma de saber si el dato que estás leyendo viene de una corrida sana es
cruzar contra las tablas de control. Si tu consumo tiene que ser estricto, chequeá `estado` antes
de servir el número; para la mayoría de los usos alcanza con vigilar `etl_control_salud`.

**4. `etl_control_salud` es una foto, no un historial.** Trae la ÚLTIMA corrida: si el ETL falló
anoche y hoy anduvo, `fallas` ya volvió a `NULL` y la falla de ayer desapareció de esa vista. Los
errores intermitentes —los peores, porque no los ves nunca en vivo— sólo están en
`etl_control_ejecucion`, que guarda una fila por corrida.

No es teórico: al 2026-09-10 `etl_control_salud` devuelve **cero** fallas, y el mismo día
`etl_control_ejecucion` tiene **11 fallas en 7 datasets** en los últimos 30 días (MAGyP sin
resolver DNS, Energía tirando 503). Un alerta montado sólo sobre `etl_control_salud` no habría
visto ninguna.

```sql
-- Fallas de los últimos 30 días, aunque después se hayan resuelto solas.
select dataset, comando, inicio, falla
from etl_control_ejecucion, unnest(fallas) as falla
where estado = 'falla' and inicio > now() - interval '30 days'
order by inicio desc;

-- ¿Qué dataset viene fallando seguido? (un 503 aislado es ruido; 8 de 30 corridas, no)
select dataset,
       count(*) filter (where estado = 'falla') as fallas,
       count(*)                                 as corridas
from etl_control_ejecucion
where inicio > now() - interval '30 days' and comando <> 'load-history'
group by dataset having count(*) filter (where estado = 'falla') > 0
order by fallas desc;
```

`python -m etl <dataset>` además sale con **código 1** si la corrida registró alguna falla, que es
lo que hace que el cron mande el mail.

### Para la app: cuál de las dos columnas alertar

Son dos preguntas distintas y conviene tratarlas distinto:

```sql
-- Alerta accionable: hay algo que arreglar de nuestro lado.
select * from etl_control_salud where estado <> 'ok';

-- Informativo: la fuente se atrasó. Sirve para explicarle al usuario por qué un
-- gráfico "no avanza", sin que parezca que el sistema está roto.
select dataset, ultimo_dato, dias_dato, dias_max_dato
from etl_control_salud where estado_dato <> 'ok';
```

`DATO_VIEJO` **no** debería disparar un alerta de guardia: lo normal es que el organismo publique
tarde. Sirve para dos cosas: mostrar en la UI que el dato está desactualizado por la fuente y no
por el pipeline, y detectar el caso silencioso en que la fuente cambió de formato y el parser la
ignora sin lanzar excepción (`estado = ok` + `estado_dato = DATO_VIEJO` sostenido varias semanas).

Los umbrales de `dias_max_dato` salen de `edad del label al publicarse + un período + margen`,
dataset por dataset. Cuidado con el primer término: **no** es el rezago de la fuente. Como `date`
es el primer día del período, el label ya viene con el período entero encima (ADEFA publica junio
el 04/07 —rezago real de 4 días— pero `ultimo_dato` vale `2026-06-01`, o sea 33 días de edad).
La derivación completa está en `help_etl.md`.

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


## `datos_gob` (API oficial del Estado) — cómo consumirlo

14 series de organismos públicos (INDEC y Secretaría de Trabajo). Es el único dataset mensual con
**star-schema**, porque sus series **no comparten unidad**: conviven índices, dólares y pesos. La
vista trae el nombre y la unidad ya unidos.

> **Dos fuentes, con prioridad explícita.** Nueve series salen de `apis.datos.gob.ar/series`. Las
> **cinco `indice_salarios_*` salen de un CSV de cuadros del INDEC**
> (`indec.gob.ar/ftp/cuadros/sociedad/indice_salarios.csv`), y para ellas la API quedó como
> **respaldo**: sólo entra si el cuadro no estuvo disponible.
>
> El motivo, medido el 2026-09-10: sobre **611 meses solapados hay 0 discrepancias**, y el CSV va
> **dos meses adelante** (traía 2026-05 y 2026-06 cuando la API todavía cortaba en 2026-04). Es el
> mismo dato por un canal que publica antes, no dos estimaciones distintas — por eso ambas entran
> con `estado = 'definitivo'` y la columna **`fuente`** dice por cuál entró cada fila:
>
> ```sql
> select serie, date, valor_nominal, fuente
> from etl_datos_gob_completo
> where serie = 'indice_salarios_total' order by date desc limit 3;
> ```
>
> Si te importa saber cuánta serie viene de cada canal:
>
> ```sql
> select case when fuente like '%ftp/cuadros%' then 'csv INDEC' else 'api series' end as canal,
>        count(*), min(date), max(date)
> from etl_datos_gob_actual where serie like 'indice_salarios%' group by 1;
> --  api series  611  2015-10-01  2026-04-01
> --  csv INDEC    10  2026-05-01  2026-06-01
> ```
>
> **`fuente` dice por dónde ENTRÓ la fila, no cuál es la fuente primaria de hoy.** El tramo viejo
> sigue marcado `api series` porque ya estaba cargado con el mismo valor cuando se hizo el cambio,
> y el modelo es append-only: `insert_if_changed` devolvió `sin_cambios` y no reescribió 611 meses
> para corregirles la etiqueta. Reescribirlos habría sido peor —611 snapshots nuevos que no
> cambian ningún número— y el valor es idéntico de todos modos. De acá en adelante los meses
> nuevos entran por el CSV.

> **Ojo con este dataset en particular al monitorear.** Son 14 series de organismos distintos,
> cada una con su calendario, y el control es por DATASET, no por serie: `ultimo_dato` es el
> `max(date)` sobre las 14, así que avanza en cuanto publica la más rápida. Una serie individual
> congelada no dispara `DATO_VIEJO`. Si te importa una serie puntual, vigilá su propio `max(date)`
> además de `etl_control_salud`:
>
> ```sql
> select serie, max(date) as ultimo, current_date - max(date) as dias
> from etl_datos_gob_completo group by serie order by dias desc;
> ```
>
> Al 2026-09-10 esa query devuelve un rango de **40 a 101 días** según la serie (`smvm` en
> 2026-08, los cinco `indice_salarios_*` en 2026-06). `etl_control_salud` ve sólo los 40 —el
> `max`— y marca `estado_dato = ok` contra un umbral de 75, sin enterarse de las que están arriba.
> El dataset está sano; el promedio de calendarios distintos no dice nada de ninguno.
>
> Ese hueco fue real, no hipotético: antes de que el CSV pasara a ser la fuente primaria, las 5
> series de salarios estaban en **2026-04, a 162 días**, con el control marcando `ok` todo el
> tiempo. Nadie se enteró hasta que alguien miró serie por serie.
>
> Y si una serie falla al bajar, el string en `fallas` la nombra
> (`datos_gob / run isac: ERROR bajando ...`): ver [Leer los errores que reportó el
> ETL](#leer-los-errores-que-reportó-el-etl).

### La vista que probablemente querés

`etl_datos_gob_completo` da **los tres valores en una sola fila**:

```sql
select serie, nombre, unidad, date, valor_nominal, valor_real, valor_desest
from etl_datos_gob_completo
where serie = 'ventas_supermercados' order by date;
```

| Columna | Qué es | Cuándo es NULL |
|---|---|---|
| `valor_nominal` | Tal como lo publica el organismo | nunca |
| `valor_real` | A precios del **último dato de esa serie** (el mes exacto viaja en `mes_base`) | si la serie no se deflacta, o el mes no tiene deflactor |
| `valor_desest` | Serie desestacionalizada: X-13 propio sobre la real, **o** la oficial del organismo | si esa serie no se desestacionaliza |
| `mes_base` | Mes cuya moneda expresa `valor_real` | si `valor_real` es NULL |
| `deflactor_origen` | `publicado`, `proyectado` o `interpolado`: procedencia del índice de ese mes | si `valor_real` es NULL |
| `deflactor` | `ipc_largo` (pesos constantes) o `uscpi_mensual` (dólares constantes) | si `valor_real` es NULL |
| `desest_fuente` | `census x13` (corrida propia) o la URL de la serie oficial | si `valor_desest` es NULL |
| `desest_parametros` | Parámetros del X-13, o `{"origen": "indec", ...}` si es la oficial | si `valor_desest` es NULL |

Cada una tiene además su vista suelta: `etl_datos_gob_actual` (nominal),
`etl_datos_gob_real`, `etl_datos_gob_desest`.

### Las 14 series

| `serie` | Unidad | Desde | ¿real? | ¿desest? |
|---|---|---|---|---|
| `isac` | índice 2004=100 | 2012-01 | — | **sí** *(oficial INDEC)* |
| `ipi_manufacturero` | índice 2004=100 | 2016-01 | — | **sí** *(oficial INDEC)* |
| `ipc_nacional` | índice dic-2016=100 | 2016-12 | — | — |
| `expo_total` | USD millones | 1992-01 | sí *(CPI EEUU)* | **sí** |
| `impo_total` | USD millones | 1992-01 | sí *(CPI EEUU)* | **sí** |
| `ventas_supermercados` | **miles** de pesos | 2017-01 | sí | **sí** |
| `ventas_centros_compras` | pesos | 2017-01 | sí | **sí** |
| `ripte` | pesos corrientes | 1994-07 | sí | — |
| `smvm` | pesos corrientes | 1965-01 | sí | — |
| `indice_salarios_total` | índice oct-2016=100 | 2016-10 | sí | — |
| `indice_salarios_registrado` | índice oct-2016=100 | 2015-10 | sí | — |
| `indice_salarios_priv_registrado` | índice oct-2016=100 | 2015-10 | sí | — |
| `indice_salarios_publico` | índice oct-2016=100 | 2015-10 | sí | — |
| `indice_salarios_priv_no_registrado` | índice oct-2016=100 | 2016-10 | sí | — |

> **`valor_desest` tiene DOS orígenes y no son intercambiables.** Para las 4 series de ventas y
> comex es el X-13 que corre este repo sobre la serie real. Para `isac` e `ipi_manufacturero` es la
> ajustada que publica INDEC: no le corremos X-13 encima a una serie que el organismo ya ajusta.
> **Cuál de los dos es, lo dice la fila** — `desest_fuente` y `desest_parametros` en
> `etl_datos_gob_completo`:
>
> ```sql
> select serie, date, valor_desest, desest_fuente, desest_parametros ->> 'origen'
> from etl_datos_gob_completo
> where valor_desest is not null and date = '2026-05-01';
> --  isac        153.39  https://apis.datos.gob.ar/...ISAC_SIN_EDAD...   indec
> --  expo_total 8772.32  census x13                                      (NULL, trae los params X-13)
> ```
>
> No compares `valor_desest` entre series sin mirar `desest_fuente`: parámetros distintos, criterio
> distinto. Una serie va por una ruta o por la otra, **nunca por las dos** — `run.py` aborta si una
> serie aparece en `DESEST_OFICIAL` (config.py) y en el bloque `[datos_gob]` de
> `etl/series_desest.toml`, porque las dos escriben la misma fila y una pisaría a la otra.

### Cuatro cosas que hay que saber

**1. `ventas_supermercados` está en MILES de pesos y `ventas_centros_compras` en pesos.** Es así
en la fuente y se guarda como la fuente lo publica. Compararlas sin normalizar da un error de
1000x. Por eso la vista trae `unidad`:

```sql
select date,
       max(valor_real) filter (where serie = 'ventas_supermercados')   / 1e3 as super_mm,
       max(valor_real) filter (where serie = 'ventas_centros_compras') / 1e6 as shopping_mm
from etl_datos_gob_completo
where serie in ('ventas_supermercados','ventas_centros_compras')
group by date order by date;
```

**2. Hay DOS deflactores, uno por moneda, y hay que mirar `deflactor_origen`.** Cuál se aplica a
cada serie lo dice la columna `deflactor`; los dos salen de `public.deflactores`:

| `deflactor` | Qué es | Desde | Series |
|---|---|---|---|
| `ipc_largo` | IPC de INDEC desde 2016-12 y, hacia atrás, `inflaempalmada` reescalada | 1990-01 | las 9 de pesos |
| `uscpi_mensual` | CPI-U del BLS (`CUUR0000SA0`, all items, **NSA**) | 1913-01 | `expo_total`, `impo_total` |

**Estar en dólares no exime de deflactar.** Un dólar de 1992 compra bastante más que uno de
2026: leer expo/impo nominales de punta a punta sobrestima el crecimiento por toda la inflación
de EEUU del medio. Sobre 34 años de serie el factor es 2,4x — enero-1992 pasa de USD 726 M
nominales a USD 1.755 M de junio-2026. No es cosmético.

Que el CPI sea **NSA** (sin desestacionalizar) es a propósito: deflactar con la versión
desestacionalizada le metería al valor real la estacionalidad del deflactor dada vuelta, y el
X-13 posterior terminaría ajustando ese artefacto además de la estacionalidad del comercio.

Con esto `ripte` tiene real desde 1994-07 (383 meses), `smvm` desde 1992-01 (416) y expo/impo
desde 1992-01 (414). Cuatro advertencias:

- `deflactor_origen = 'proyectado'` marca los meses cuyo índice todavía no salió y se estimó. Hoy
  es sólo `smvm`, que publica antes que el IPC. **Esos valores se revisan** cuando INDEC
  publica: no presentarlos como firmes. En dólares casi no aparece: el BLS publica el CPI de un
  mes a mitad del siguiente, antes que INDEC el comercio exterior de ese mismo mes.
- `deflactor_origen = 'interpolado'` es exclusivo de `uscpi_mensual`: mes que el BLS no publicó y
  se estimó por interpolación geométrica. Hoy es **uno solo, octubre-2025** (shutdown), y cae
  dentro del rango de expo/impo. Es una cuenta nuestra, no un dato del BLS.
- **`smvm` no tiene valor real antes de 1992-01** aunque el nominal llegue a 1965. Su monto está
  en la moneda de curso legal de cada época, y esa moneda cambió tres veces (1983-06, 1985-07 y
  1992-01). En dic-1991 el nominal dice 970.000 y en ene-1992 dice 97: no bajó el salario,
  cambió la unidad. El deflactor no ve esas reformas, así que deflactar australes daría un valor
  10.000 veces más grande. El corte vive en `real_desde` de `etl_datos_gob_series`.
- El tramo anterior a 2016-12 arrastra el redondeo a entero de `inflaempalmada`: ~0,2% en 2010 y
  ~0,66% en 1995-2002. Tolerable para niveles; para leer variaciones mes a mes de los 90, no.

`deflactores`, `ipc_largo` y `uscpi_mensual` los mantiene otro repo
(`downloaders_viejos/downloader`), no este.

**3. El mes base es MÓVIL y POR SERIE: moneda del último dato de cada una.** Cada serie se
expresa en la moneda de su propio último mes observado, y toda su historia se reexpresa hacia
atrás en esa moneda. En el mes base, `valor_real = valor_nominal` exactamente.

**Las 11 no comparten base**, porque no terminan el mismo mes:

| `mes_base` | `deflactor` | series |
|---|---|---|
| 2026-08 | `ipc_largo` | `smvm` (publica antes que el IPC) |
| 2026-06 | `uscpi_mensual` | `expo_total`, `impo_total` |
| 2026-05 | `ipc_largo` | `ripte`, `ventas_supermercados`, `ventas_centros_compras` |
| 2026-04 | `ipc_largo` | los 5 `indice_salarios_*` |

**Mismo `mes_base` Y mismo `deflactor` → comparables directo**, sin hacer nada: las tres de
2026-05 entre sí, los 5 índices de salarios entre sí, expo contra impo. Si difiere el `mes_base`
hay que reescalar por `indice(base_a)/indice(base_b)` — hoy el par que importa es `ripte`
(2026-05) contra `smvm` (2026-08). Si difiere el **`deflactor`**, no se comparan niveles sin
pasar por un tipo de cambio: pesos constantes y dólares constantes son unidades distintas, no
dos escalas de la misma.

La otra consecuencia: **cuando entra un mes nuevo, esa serie real se reescala entera.** Los
niveles cambian, las variaciones no. Guardate `mes_base` junto al dato si necesitás reproducir
un número viejo.

El deflactor entra completo, observado **y proyectado**: las proyecciones de
`inflacion_proyectada` son las que permiten deflactar los meses que el IPC todavía no cubre.
Hoy eso hace que el base de `smvm` sea un mes proyectado, así que corregir esa proyección mueve
los niveles de toda su historia (las variaciones, no). `deflactor_origen` lo marca.

**4. El desestacionalizado se calcula sobre la serie REAL, no sobre la nominal.** Bajo inflación
argentina la estacionalidad de una serie en pesos corrientes queda tapada por la deriva de
precios. El factor estacional que sale para supermercados es el esperable:

| mes | 01 | 02 | 03 | ... | 09 | ... | 12 |
|---|---|---|---|---|---|---|---|
| factor | 0,99 | 0,94 | 1,01 | | 0,93 | | **1,22** |

> **Nuestro número NO coincide con el de INDEC, y es a propósito.** INDEC publica su propia serie
> de estas dos, a precios constantes, en la misma API: `455.1_VENTAS_PREADA_0_M_44_44`
> (supermercados) y `458.1_VENTAS_TOTADA_0_M_52_56` (centros de compras), índice 2017=100. No las
> adoptamos ni recalibramos el X-13 para reproducirlas. **Si comparás contra el informe de INDEC,
> los números no van a dar iguales** — esto explica por qué.
>
> **La causa principal es el deflactor, no el X-13.** Nosotros deflactamos con **IPC nacional**
> (`ipc_largo`) y base móvil; INDEC deflacta con su **índice sectorial** propio. Eso se puede
> medir por separado, porque INDEC también publica la serie deflactada SIN ajustar
> (`455.1_VENTAS_PRENAL_0_M_34_10`). Comparando variación mensual, sin la pandemia:
>
> | comparación | mediana |
> |---|---|
> | nuestra `valor_real` vs la deflactada de INDEC — **sólo el deflactor** | 0,420 pp |
> | nuestra `valor_desest` vs el desest de INDEC — **deflactor + X-13** | 0,820 pp |
>
> Antes de que el X-13 toque nada, la serie real ya se separa 0,42 pp. Las dos causas son del
> mismo orden: no es que el ajuste estacional esté mal calibrado, es que partimos de otra serie
> real. En supermercados los peores meses son diciembres (2022-12, 2023-12, 2025-12), el pico
> estacional.
>
> **Por qué se eligió así:** las 11 series deflactadas de esta tabla comparten método y base
> móvil, y por eso se comparan entre sí sin asteriscos. Alinear dos de ellas con INDEC las
> desalinearía de sus propias compañeras de tabla, que es el uso real de esta tabla. Se prioriza
> la homogeneidad interna sobre la coincidencia con la fuente.
>
> Consecuencia práctica: `valor_real` y `valor_desest` de estas series son **nuestro** número, no
> el de INDEC. Sirven para ver la evolución y comparar contra las otras series del dataset. **No
> los cites como oficiales**, y si necesitás el número oficial, bajá los ids de arriba.

### Salario real (`ripte`, `smvm`, `indice_salarios_*`)

> **`ripte` ya viene sin aguinaldo, y eso NO es un prorrateo.** La Secretaría de Seguridad
> Social divide el promedio mensual por coeficientes fijos —enero 1,05, junio 1,50, diciembre
> 1,54, resto 1,00— para **deducir** el SAC y el plus vacacional (Resolución 2-E/2018, Anexo II).
> O sea que `ripte` mide la remuneración mensual **regular**, no el ingreso anual repartido en 12.
> Dos consecuencias: (1) la serie no necesita X-13 porque ya viene desestacionalizada de origen,
> aunque por coeficientes fijos desde 2006 y no por ajuste estadístico, así que deja residuo
> (diciembre real da 0,973 contra noviembre: el 1,54 sobre-corrige un poco); (2) para pasar a
> ingreso anual no alcanza con multiplicar por 12.

`ripte` y `smvm` son montos en pesos corrientes; los `indice_salarios_*` son índices nominales.
Los nueve deflactables ya vienen resueltos en `valor_real`, a **pesos del último dato de cada
serie**, así que el salario real sale directo:

```sql
select date, valor_nominal, valor_real, deflactor_origen
from etl_datos_gob_completo
where serie = 'ripte' and valor_real is not null
order by date desc limit 12;
```

**Hasta dónde llega cada una.** El deflactor (`ipc_largo`) arranca en 1990-01, y ése es el
límite real, no el de la serie nominal:

| serie | nominal | con `valor_real` | qué queda afuera |
|---|---|---|---|
| `ripte` | 1994-07 → | **1994-07 →** (383 meses) | nada |
| `smvm` | 1965-01 → | **1992-01 →** (416 meses) | 1965-1991: `valor_real` es NULL (otra moneda) |
| `indice_salarios_*` | 2015-10 / 2016-10 → | igual que el nominal | nada |

**Las tres reglas para consumirlas.**

**1. Filtrá siempre por `deflactor_origen`.** `'proyectado'` marca los meses cuyo IPC todavía no
publicó INDEC: el `valor_real` se calculó con una estimación cargada a mano y **se va a mover**.
Hoy pasa sólo con `smvm`, que publica su monto antes que el IPC del mes.

```sql
-- salario real firme: sólo meses con IPC ya publicado
select date, valor_real
from etl_datos_gob_completo
where serie = 'smvm' and deflactor_origen = 'publicado'
order by date;
```

Para un gráfico donde igual querés mostrar el último mes, traelo pero distinguilo (línea
punteada, marcador hueco, lo que sea). Nunca lo mezcles en silencio con los publicados.

**2. No busques `smvm` real antes de 1992: no existe, y es correcto que no exista.** El nominal
llega a 1965, pero está expresado en la moneda de cada época y la moneda cambió tres veces
(peso ley → peso argentino en 1983-06, → austral en 1985-07, → peso convertible en 1992-01). El
deflactor es un índice de poder adquisitivo y atraviesa esas reformas sin saltos, así que
deflactar el tramo viejo daría números inflados por el factor de conversión. El corte está en
`etl_datos_gob_series.real_desde` y la vista lo aplica sola.

Si necesitás el tramo 1965-1991, la conversión la hacés vos sobre `valor_nominal`, que sigue
intacto y es lo que publica el organismo.

```sql
-- qué series tienen piso de valor real, y desde cuándo
select serie, real_desde from etl_datos_gob_series where real_desde is not null;
```

Ojo también con las variaciones mes a mes en los 90: el deflactor de ese tramo viene de
`inflaempalmada`, con el índice redondeado a entero (~0,66% de error en 1995-2002). Para
niveles va bien; para variaciones mensuales de esos años, no.

**3. El base se mueve y es por serie: guardate `mes_base` con el dato.** Cada `valor_real` está
en pesos del último mes de SU serie, así que cuando entra un mes nuevo los niveles de esa serie
se reescalan enteros (las variaciones no). Si comparás un número que guardaste hace tres meses
contra la vista de hoy, no van a coincidir: no es un error, es otro base. Para reexpresar en el
base nuevo, multiplicá por `IPC(base_nuevo)/IPC(base_viejo)`. Entre dos series distintas, si
`mes_base` coincide no hay nada que hacer.

> El deflactor es `public.deflactores` con `deflactor='ipc_largo'`, que **mantiene otro repo**
> (`downloaders_viejos/downloader`, vía `IPCDownload.py`). Si ese ETL se cae, estas vistas
> devuelven datos viejos sin avisar. Para chequear frescura del deflactor:
> `select max(fecha) from deflactores where deflactor='ipc_largo' and origen='publicado';`

> Sumar una serie nueva **no requiere escribir código**: se agrega su id a `SERIES_META` en
> `etl/datasets/datos_gob/config.py` (con su flag `deflactable`) y la dimensión se re-sincroniza
> sola en la próxima corrida. Las trampas de la API están en `docs/datos_gob_ar.md`.

## `comex` (índices de comercio exterior del INDEC) — cómo consumirlo

Son **números índice base 2004=100**, mensuales desde 2004-01, del Índice de Comercio Exterior
(ICA) del INDEC. Descomponen el comercio exterior en sus dos componentes: **cuánto se despachó**
(`cantidad`) y **a qué precio** (`precio`), con `valor` = el producto de ambos.

### La vista que probablemente querés

`etl_comex_actual` ya trae la dimensión unida, así que se filtra por eje sin parsear el nombre:

```sql
-- Volumen exportado por rubro, desestacionalizado, último año
select d.rubro, d.date, d.valor
from etl_comex_desest d
where d.flujo = 'expo' and d.date >= date '2025-08-01'
order by d.rubro, d.date;

-- Precio vs cantidad de las exportaciones: ¿vendimos más o sólo más caro?
select date,
       max(valor) filter (where indice = 'cantidad') as cantidad,
       max(valor) filter (where indice = 'precio')   as precio,
       max(valor) filter (where indice = 'valor')    as valor
from etl_comex_actual
where flujo = 'expo' and rubro = 'general'
group by date order by date;
```

### Cuatro cosas que hay que saber

**1. No confundir con `expo_total` / `impo_total` de `datos_gob`.** Aquéllas son **montos en
dólares**; éstas son **índices**. Un monto que sube no dice si se exportó más o si subió el
precio internacional — para eso están estas. Se complementan, no se reemplazan.

**2. `cantidad` es lo único desestacionalizado, y es a propósito.** El volumen físico es lo que
trae la estacionalidad de la cosecha, y es la serie que tiene sentido comparar contra el mes
anterior. `precio` no tiene estacionalidad de calendario que sacarle, y desestacionalizar
`valor` por separado rompería la identidad `valor = precio × cantidad` sin dar nada a cambio.
Para comparar contra el mes anterior usá `etl_comex_desest`; para comparar contra el mismo mes
del año anterior, la serie observada alcanza.

**3. Los últimos años son PROVISORIOS y se revisan.** INDEC marca años enteros como provisorios
(al 2026-08: 2024, 2025 y 2026) y los cierra después. `etl_comex_actual` prefiere el snapshot
`definitivo` cuando existe, pero mientras tanto el `estado` de la fila dice `provisorio`: si
guardás un número de esos, puede cambiar.

**4. La base es 2004=100 y no es negociable en la serie cargada.** Si INDEC rebasea, el ETL
**corta** en vez de apendear valores de otra base (ver `source._check_base` y el README). Un
salto de nivel sin marcar es peor que un dato faltante.

> Las importaciones sólo están a **nivel general**: INDEC no abre los grandes rubros del lado
> importador en esta serie mensual (los agrupa por uso económico, que es otro cuadro y otra
> planilla). Si algún día hace falta, es un dataset nuevo, no una columna más acá.

## `ventas_combustibles` (Sec. Energía) — cómo consumirlo

Ventas al mercado interno de derivados del petróleo, mensual desde **2010-01**. Es la contracara
de `hidrocarburos`: aquél mide **producción** de crudo y gas, éste mide **qué se comercializa**.

Se guardan los **51 productos** que publica la fuente, no un total precalculado. El agregado se
deriva; el grano no. Eso es lo que te deja armar cualquier total sin esperar un backfill.

### La vista que probablemente querés

Como en el resto del repo, **`etl_ventas_combustibles_actual` trae el grano Y los agregados**, y
se distinguen por `tipo`: los 51 productos vienen con `tipo` en `refinado`/`crudo`/`gaseoso` y los
6 agregados con **`tipo = 'agregado'`** (y `estado = 'derivado'`, porque se calculan, no se
ingestan). Eso es lo que hace que `series_actual` y `series_desest` se puedan unir por
`(dataset, serie, date)` igual que en cualquier otro dataset.

**`etl_ventas_combustibles_totales`** es el atajo: sólo los agregados, cada uno con su unidad.
Y **`etl_ventas_combustibles_productos`** es el atajo contrario: sólo el grano.

| serie | qué suma | unidad |
|---|---|---|
| `gasoil_mas_nafta` | gasoil + nafta, las dos familias de combustible líquido | m3 |
| `gasoil` | los 4 tipos de gasoil | m3 |
| `nafta` | los 4 tipos de nafta | m3 |
| `total_refinados_m3` | los 26 refinados que se miden en volumen | m3 |
| `total_refinados_ton` | los que se venden por peso (fueloil, coque, asfaltos, GLP…) | Ton |
| `glp` | butano + propano — **GLP de garrafa y granel, NO gas de red** | Ton |

```sql
-- Consumo de combustible de surtidor, últimos 24 meses
select date, valor
from etl_ventas_combustibles_totales
where serie = 'gasoil_mas_nafta'
order by date desc limit 24;

-- Gasoil vs nafta, para ver quién arrastra
select date,
       max(valor) filter (where serie = 'gasoil') as gasoil,
       max(valor) filter (where serie = 'nafta')  as nafta
from etl_ventas_combustibles_totales
where serie in ('gasoil','nafta')
group by date order by date;
```

`gasoil_mas_nafta` se llama así y no "automotor" a propósito: adentro está el `gasoil_g1_agro`
(agrogasoil, que va a tractores y cosechadoras) y buena parte del gasoil común se consume en
transporte de carga, agro e industria. No es "combustible de autos", es la suma de las dos
familias de combustible líquido. Excluye aviación y excluye solventes, donde vive la nafta
virgen —materia prima petroquímica, no combustible—.

> **Casi siempre vas a querer `gasoil` y `nafta` por separado, no la suma.** Tienen estacionalidad
> **opuesta**: en enero el gasoil está 3,5% por DEBAJO de su tendencia y la nafta 3,6% por ENCIMA.
> El agregado esconde eso. Las dos están disponibles como series propias, observadas
> (`etl_ventas_combustibles_totales`) y desestacionalizadas (`etl_ventas_combustibles_desest`).

```sql
-- Gasoil y nafta desestacionalizadas, cada una por su lado
select date,
       max(valor) filter (where serie = 'gasoil') as gasoil,
       max(valor) filter (where serie = 'nafta')  as nafta
from etl_ventas_combustibles_desest
where serie in ('gasoil','nafta')
group by date order by date;
```

### Armar tu propio total

**`etl_ventas_combustibles_actual`** es el grano, con la dimensión ya pegada por JOIN: cada fila
trae `nombre`, `unidad`, `tipo` (`refinado` / `crudo` / `gaseoso`) y `familia` (`gasoil`, `nafta`,
`aviacion`, `glp`, `pesados`, `lubricantes`, `solventes`, `otros`, `gas`, `crudo`).

```sql
-- Tu total: refinados en m3, incluyendo aviación pero sin solventes ni lubricantes
select date, sum(valor) as m3
from etl_ventas_combustibles_actual
where unidad = '(m3)'
  and tipo   = 'refinado'
  and familia in ('gasoil','nafta','aviacion','otros','pesados')
group by date order by date;

-- Qué productos hay y cómo están clasificados
select serie, nombre, unidad, tipo, familia
from etl_ventas_combustibles_series order by orden;

-- Un producto puntual
select date, valor from etl_ventas_combustibles_actual
where serie = 'gasoil_g2_comun' order by date;
```

**La regla:** todo total filtra **siempre** por `unidad`, y salvo que quieras crudo, también por
`tipo = 'refinado'`.

> **Y nunca sumes `etl_ventas_combustibles_actual` entero.** Ahí conviven el grano y los
> agregados, así que un `sum(valor)` sin filtrar cuenta doble — es la misma situación que `granos`
> (donde `total` convive con los 7 granos) o `comex` (donde el nivel general convive con sus
> rubros). Filtrá `tipo = 'agregado'` para quedarte con los totales, o `tipo <> 'agregado'` para
> el grano. O usá directamente `_totales` / `_productos`.

### Cinco cosas que hay que saber

**1. Hay CRUDO adentro del dataset de ventas, y el chart oficial lo suma.** 15 de los 51
"productos" son petróleo por cuenca (`Cuenca Neuquina - Neuquen (Medanito)`, …) más
`Crudo importado`. Pesan hasta **6,6% del total en 2010**, ~1,5% en 2022 y **0 desde 2025-01**.
Están guardados —son un dato— con `tipo = 'crudo'` y **fuera de todos los totales de arriba**. Si
tomás el total del dashboard tal cual, la serie 2010-2024 te queda inflada y con una baja
tendencial falsa, producida por la desaparición del crudo y no por el consumo. Como hoy es cero,
mirando sólo los meses recientes no lo detectás nunca.

**2. Conviven TRES unidades en la misma columna `valor`:** `(m3)` los líquidos, `(Ton)` los
pesados y el GLP, `(miles/m3)` los gaseosos. Un `sum(valor)` sin filtrar por `unidad` suma metros
cúbicos con toneladas. La unidad está en la dimensión, nunca en el nombre de la serie.

**3. `glp` NO es el gas natural de red.** Es gas licuado de petróleo —butano + propano, en
toneladas—: garrafa y granel. El gas de red está aparte, en la familia `gas` (`gas_natural`,
`gas_refineria`, `gnl`), se mide en miles de m3 y **no entra en ningún total**. Verificado: `glp`
suma exactamente butano + propano y nada más.

**4. `nafta_virgen` no es nafta.** Los nombres de familia son la
clasificación **nuestra**, no la de la fuente. `nafta_virgen` es petroquímica y está en
`solventes`; `diesel_oil` y `kerosene` están en `otros`, no en `gasoil`. Si tu definición difiere,
mirá `etl_ventas_combustibles_series` antes de asumir.

**5. Para comparar mes contra mes, usá `etl_ventas_combustibles_desest`.** Se ajustan `gasoil`,
`nafta`, `glp` y `asfaltos`, y `gasoil_mas_nafta` sale de **sumar las dos primeras ya
ajustadas** (ajuste indirecto): nafta pica en verano y gasoil en primavera, así que al sumarlas en crudo se cancelan
y el ajuste directo del agregado rompería la identidad `gasoil_mas_nafta = gasoil + nafta`. La
identidad se cumple **exacta** en los 199 meses; las filas derivadas se distinguen por
`parametros->>'metodo' = 'indirecto'`.

```sql
-- Consumo de surtidor sin estacionalidad
select date, valor from etl_ventas_combustibles_desest
where serie = 'gasoil_mas_nafta' order by date;
```

Cuánto trabaja el ajuste (|d11 − obs| / obs): `glp` 21,0% medio, `asfaltos` 8,6%, `nafta` 4,0%,
`gasoil` 3,6%, `gasoil_mas_nafta` 2,9%. Vale la pena: diciembre→enero-2026 el crudo marca
**−5,3%** y el ajustado **+1,2%** — signo opuesto. Ojo con `glp`: es la de estacionalidad más
fuerte (amplitud 67%) pero **ninguna** configuración de X-13 sale limpia (todas reportan
estacionalidad móvil, y M3 y M5 quedan fuera de rango). Q=0,77 es usable, pero es la de menor
calidad de las cuatro.

**`asfaltos` es el único producto suelto que se ajusta**, y no mide consumo: es insumo de obra
vial, así que sigue el calendario de obra. El parate de fin de año es enorme —diciembre 0,831 y
enero 0,867 contra picos en septiembre (1,093) y agosto (1,089), amplitud 31,5%— y en crudo
aparece todos los años: noviembre→diciembre-2025 marca **−30,1%**, y ajustado **−12,5%**.
Ninguno de los otros 50 productos se ajusta: la lectura útil del dataset es por familia, y la
familia de `asfaltos` (`pesados`) mezcla fueloil, coque y destilado de vacío, que no tienen nada
que ver con obra vial.

> El último mes que publica la fuente aparece **con 0 en todos los productos** antes de estar
> listo. El ETL descarta esos meses (un mes entero en cero es el placeholder, no un derrumbe del
> consumo), así que en la base no vas a verlo — pero si consultás el dashboard directo, sí.

## `refinacion` (Sec. Energía) — cómo consumirlo

**Insumos que entran a las refinerías**, mensual desde **2010-01**: crudo por cuenca más el resto
de las corrientes que se procesan (biocombustibles, cortes, mejoradores de octano). Unidad única:
**metros cúbicos**.

> **El dashboard de la fuente se llama "Productos procesados" y son INSUMOS, no productos
> terminados.** Lo que *sale* de la refinería está en otro dataset de la Secretaría (dashboard 97,
> "Subproductos obtenidos") y **no** está en este repo. Se distinguen por los conceptos: acá dicen
> `Cuenca Neuquina - Neuquen (Medanito)` y `Biodiesel`; allá, `Gasoil Grado 2 (Común)`.

### La vista que probablemente querés

```sql
-- Crudo procesado, la serie que se sigue
select date, valor from etl_refinacion_totales
where serie = 'crudo_procesado' order by date;
```

Agregados disponibles en `etl_refinacion_totales`:

| serie | qué suma |
|---|---|
| `crudo_procesado` | los 15 conceptos de crudo — el *crude run* clásico |
| `otros_insumos` | los 21 restantes: biocombustibles, cortes, mejoradores |
| `total_procesado` | todo lo que entró a refinería |
| `crudo_neuquina`, `crudo_golfo_san_jorge`, `crudo_austral`, `crudo_noroeste`, `crudo_cuyana`, `crudo_importado` | el crudo abierto por cuenca de origen |

Como en el resto del repo, **`etl_refinacion_actual` trae el grano Y los agregados**, distinguidos
por `tipo='agregado'` (y `estado='derivado'`). `etl_refinacion_conceptos` es el atajo al grano solo.

### Tres cosas que hay que saber

**1. `crudo_procesado` NO es `total_procesado`, y la brecha crece.** El crudo era el **85,5%** de
lo procesado en 2010 y es el **78,9%** en julio-2026. La diferencia la explican los
biocombustibles, que crecieron con los cortes obligatorios. Usar el total como si fuera crudo
procesado sobreestima, y cada año un poco más. Son series distintas a propósito.

**2. Nunca sumes `etl_refinacion_actual` entero.** Conviven grano y agregados: `sum(valor)` sin
filtrar cuenta doble, y los agregados por cuenca cuentan triple (están dentro de
`crudo_procesado`, que está dentro de `total_procesado`). Filtrá `tipo='agregado'` y una serie
puntual, o `tipo <> 'agregado'` para el grano.

**3. La `familia` del crudo es la cuenca de origen**, que deja ver el corrimiento de la dieta de
las refinerías:

```sql
-- Participación de cada cuenca en el crudo procesado
select date, familia, sum(valor) as m3
from etl_refinacion_actual
where tipo = 'crudo'
group by date, familia order by date, familia;
```

> **Este dataset no se desestacionaliza, y se decidió midiéndolo.** `crudo_procesado` tiene una
> estacionalidad chica (amplitud 5,5%) y **que se muda de mes**: el pico está en septiembre en
> 2010-2015 y en diciembre después. Los diagnósticos de X-13 dan F de estacionalidad estable 8,1
> —un orden de magnitud por debajo del resto del repo— y **M1 falla en las seis configuraciones
> probadas**, o sea que el irregular le gana a la señal estacional. La causa es física: las
> refinerías paran por mantenimiento, pero no en el mismo mes cada año, y eso produce un irregular
> grande, no un patrón estacional. El ajuste movería los valores 2,8% y sacaría apenas el 24% del
> ruido mensual. `etl_refinacion_desest` existe y devuelve 0 filas; el razonamiento completo está
> en `etl/series_desest.toml`.
>
> **Para comparar contra el mes anterior**, entonces, usá variación interanual.

## `transferencias` (DNRPA) — cómo consumirlo

**Transferencias de automotores**: cambio de titular de un vehículo que ya estaba en el parque, o
sea el **mercado de usados**. Total país, mensual desde **1995-01** (380 meses). Unidad:
trámites.

```sql
-- La serie, cruda y ajustada
select a.date, a.valor as tramites, d.valor as desest
from etl_transferencias_actual a
left join etl_transferencias_desest d using (serie, date)
where a.serie = 'autos' order by a.date;
```

### Los tres datasets de autos no se pisan

| Dataset | Qué mide | Fuente | Desde |
|---|---|---|---|
| `automotriz` | producción, ventas **mayoristas** y expo de **fábrica** | ADEFA | 1994-01 |
| `patentamientos` | registraciones **0km** | SIOMAA | 2022-01 |
| `transferencias` | **usados**: cambio de titular | DNRPA | 1995-01 |

Son universos distintos y se pueden mirar juntos sin contar dos veces. El ratio
`transferencias / patentamientos` es la lectura clásica de cuántos usados se venden por cada 0km
patentado, pero ojo con el arranque: `patentamientos` sólo llega a 2022.

### Tres cosas que hay que saber

**1. Todo es `provisorio`, y no es un dataset a medio hacer.** La DNRPA no marca ningún mes como
cerrado: los registros seccionales siguen informando después del cierre y el cuadro se corrige
**hacia arriba**. Medido, la revisión toca **sólo el último mes** y es chica — agosto-2026 pasó de
155.246 a 155.717 (+0,3%) mientras los 375 meses anteriores no se movieron ni un trámite. No hay
`definitivo` que esperar; si filtrás por ese estado te llevás cero filas.

**2. Usá la desestacionalizada para el m/m.** El efecto calendario es real: con el perfil implícito
(observado ÷ d11) agosto está 9,2% arriba y febrero 12,4% abajo — amplitud pico-valle **24,7%**.
Diciembre→enero sube **+8,1%** en crudo todos los años (mediana de 31 pares) y **+0,3%** en la
ajustada: casi todo ese salto es calendario, no mercado. El ajuste saca un tercio del ruido
mensual (desvío del m/m 12,9% → 8,6%, excluyendo 2020).

**3. Abril-2020 rompe cualquier escala, y el ajuste no lo tapa.** La cuarentena dejó el mes en
**18.034** trámites contra 95.198 en marzo y ~130.000 en un mes normal: es el mínimo histórico por
lejos (el siguiente es 33.874, dic-2001). En la ajustada queda en **25.119** — el ajuste corrige lo
que le corresponde (calendario y Pascua, que en 2020 cayó justo en abril) y **no** suaviza el
shock, que es lo correcto: X-13 saca estacionalidad, no outliers. Así que el pozo está en las dos
series. Si tu gráfico necesita una escala legible, ese punto es el que la rompe.

> **Es la única serie del repo con regresor de Pascua** (`easter[1]` en el cuadro). Semana Santa se
> mueve entre marzo y abril, así que su efecto no es estacionalidad y el filtro no lo puede sacar:
> transferir un auto es un trámite presencial y una semana con dos feriados se ve en el conteo. Sin
> el regresor la calibración se queda en 0,98% de error contra la referencia; con él, en 0,0000%.
> El detalle está en `etl/series_desest.toml`.

## `fob_granos` y el PRP de granos — cómo consumirlo

Dos cosas conviven acá y conviene no confundirlas:

1. **`etl_fob_granos`**, el dataset diario: precios FOB oficiales de granos que publica MAGyP.
   Es un ETL como cualquier otro del repo.
2. **El PRP** (precio relativo de los productos): un **cálculo derivado** que combina ese FOB con
   el tipo de cambio, el IPC y los derechos de exportación. Vive todo en vistas
   (`granos_prp`, `granos_prp_combinado`) y en cuatro tablas de referencia que se mantienen a
   mano (`dex`, `vbp_granos`, `tc_granos`, `fob_granos_override`).

### El dato crudo: precios FOB oficiales (MAGyP)

| Tabla / vista | Qué trae |
|---|---|
| `etl_fob_granos` | append-only, **varias filas por (producto, día)**: una por ventana de embarque |
| `etl_fob_granos_actual` | último snapshot por (producto, día, ventana) |
| `etl_fob_granos_diario` | **una fila por (producto, día)**: el precio del embarque *spot* |
| `etl_fob_granos_mensual` | **promedio mensual** sobre los días hábiles cotizados |
| `etl_fob_granos_sin_dato` | días hábiles que la fuente declaró vacíos (feriados). No es una serie: es un registro de lo ya preguntado |
| `fob_granos_mensual` | el mensual **con los overrides aplicados**. Es lo que consume el PRP |

Cuatro productos, todos en la variante **a granel con hasta un 15 % embolsado**:

| `producto` | NCM | Posición completa | Descripción de la fuente |
|---|---|---|---|
| `soja` | 1201-90-00 | `12019000190C` | Habas de soja, Los Demás |
| `trigo` | 1001-99-00 | `10019900110W` | Trigo, Trigo Pan |
| `maiz` | 1005-90-10 | `10059010190Y` | Maíz, Los demás. En grano. |
| `girasol` | 1206-00-90 | `12060090910Y` | Semilla de Girasol, únicamente para industria, Los Demás |

> **La presentación importa.** Cada NCM publica además la versión embolsada, que cotiza ~20
> USD/ton más cara. Elegir el sufijo equivocado corre la serie entera sin que nada falle.

**La fuente cotiza una curva forward, no un precio.** Cada día hábil MAGyP publica, para cada
posición, varias filas con distinta ventana de embarque (`embarque_desde` / `embarque_hasta`).
`etl_fob_granos` las guarda **todas**; `etl_fob_granos_diario` se queda con la que **cubre el mes
de la cotización** y, si ninguna lo cubre (pasa a fin de mes, cuando la fuente ya sólo cotiza
meses siguientes), con la más próxima hacia adelante.

Esto no es un detalle: la curva está en **contango**, así que promediar todas las ventanas del
día en lugar de tomar el spot infla el FOB de soja ~11 USD/ton. Por eso la cadena
`_diario` → `_mensual` —y no un `avg()` sobre la tabla— es la que alimenta al PRP.

```sql
-- FOB spot de los cuatro granos en un día
select producto, valor, embarque_desde, embarque_hasta, circular
from etl_fob_granos_diario
where date = (select max(date) from etl_fob_granos_diario)
order by producto;

-- Promedio mensual (con cuántos días se calculó)
select date, valor, dias
from etl_fob_granos_mensual
where producto = 'soja' and date >= '2026-01-01'
order by date;
```

> `dias` importa: el mes en curso trae el promedio **parcial** de los días transcurridos.
> Filtrá por `dias` si necesitás sólo meses cerrados.

**Fines de semana y feriados no tienen filas.** La API responde con una lista vacía y el ETL lo
cuenta como `no_publicado`, no como falla. Un agujero de un día es normal; una ventana entera sin
datos sí es la fuente caída y hace fallar la corrida.

Cada día vacío queda anotado en **`etl_fob_granos_sin_dato`**, que es lo que evita volver a
gastar un request en él (ver *Carga histórica*). Para distinguir "la fuente no cotizó" de "nunca
se preguntó":

```sql
-- Días hábiles del último año que todavía no se preguntaron
select g::date as dia
from generate_series(current_date - interval '1 year', current_date, interval '1 day') g
where extract(isodow from g) < 6
  and g::date not in (select date from etl_fob_granos)
  and g::date not in (select date from etl_fob_granos_sin_dato);
```

### El cálculo: PRP

```
FOB REAL = fob_usd * tc / ipc_1993
PRP      = FOB REAL * (1 - dex)
PRP combinado = Σ (prp_grano * vbp_grano)   sobre soja, trigo, maíz y girasol
```

El resultado está en **pesos de 1993 por tonelada**. Las dos vistas:

> **Las dos son MATERIALIZADAS.** Consultarlas cuesta ~40 ms; la cadena de vistas que las
> alimenta (`granos_prp_calc`) cuesta ~1.800 ms, y el 43 % de eso es `deflactores`, que no es de
> este repo y se escanea dos veces. Se refrescan al final de cada corrida de
> `python -m etl fob_granos` (cron diario a las 9:00), siempre, haya datos nuevos o no — porque
> el PRP también depende de `deflactores`, `A3500`, `dex`, `vbp_granos` y `tc_granos`, que
> cambian sin que este ETL corra.
>
> **Si editás `dex`, `vbp_granos` o `tc_granos` desde el CRUD, el cambio no se ve hasta el
> refresh.** Para verlo ya:
> ```sql
> refresh materialized view concurrently granos_prp;
> refresh materialized view concurrently granos_prp_combinado;
> ```
> En ese orden: la segunda lee de la primera. `granos_prp_calc` y `granos_prp_combinado_calc`
> siguen existiendo como vistas normales, si hace falta el valor al instante sin refrescar.

| Vista | Qué trae |
|---|---|
| `granos_prp` | una fila por (producto, mes) con **todos los pasos intermedios**: `fob_usd`, `tc`, `ipc_1993`, `fob_real`, `dex`, `prp`. Más las marcas de procedencia `fob_origen`, `tc_motivo` y `deflactor_origen` |
| `granos_prp_combinado` | una fila por mes con `prp_soja`, `prp_trigo`, `prp_maiz`, `prp_girasol` y `prp_combinado`, más `fob_overrides` (cuántos de los cuatro vinieron pisados a mano) |

```sql
-- PRP combinado y sus cuatro componentes
select fecha, prp_soja, prp_trigo, prp_maiz, prp_girasol, prp_combinado
from granos_prp_combinado
where deflactor_origen = 'publicado'
order by fecha;

-- Un grano, con el cálculo abierto para auditarlo
select fecha, fob_usd, tc, tc_motivo, ipc_1993, fob_real, dex, prp
from granos_prp
where producto = 'soja' and fecha >= '2024-01-01'
order by fecha;
```

`granos_prp_combinado` devuelve un mes **sólo si los cuatro granos tienen PRP**. Un mes
incompleto quedaría subponderado, y ése es justo el error que comete la planilla de referencia
en los meses de cola (devuelve 0 en lugar de nada).

### Las cuatro tablas de referencia (se mantienen a mano)

Las tres primeras tienen la **misma forma**: `producto` (salvo `tc_granos`, que es única para
los cuatro), `desde`, `hasta`, el valor, y tramos que **no se solapan**. El tramo vigente lleva
`hasta = 2999-12-31`.

| Tabla | Qué guarda | Unidad |
|---|---|---|
| `dex` | derechos de exportación (retenciones) por grano | tanto por uno: `0.26` = 26 % |
| `vbp_granos` | ponderadores de la canasta por valor bruto de producción | tanto por uno; los cuatro suman 1 |
| `tc_granos` | tipo de cambio del exportador, **sólo las excepciones al A3500** | pesos por dólar |
| `fob_granos_override` | FOB mensual puesto a mano, **sólo los meses que se quieren pisar** | USD por tonelada |

`fob_granos_override` es la única de las cuatro que no usa tramos: una fila por `(producto, mes)`,
con el `date` en el primer día del mes. **Hoy está vacía** — es una válvula de escape, no una
fuente de datos. La aplica la vista **`fob_granos_mensual`**, que expone
`valor` (el efectivo), `valor_etl` (el promedio calculado, que nunca se pierde) y `origen`
(`etl` / `override`). `granos_prp` lee de ahí y arrastra la marca en `fob_origen`;
`granos_prp_combinado` cuenta cuántos componentes vinieron pisados en `fob_overrides`.

> **`etl_fob_granos_mensual` no se toca.** El override vive una capa más arriba, y por eso la
> vista que lo aplica va **sin prefijo `etl_`**: la convención del repo es que `etl_*` es lo que
> bajó el ETL y lo demás es lo que se mantiene a mano —igual que `dex`, `vbp_granos` y
> `tc_granos`—. La columna `motivo` es obligatoria: un override sin explicación es un dato
> perdido dentro de seis meses.

**Cómo se extiende un tramo.** Cerrar el vigente y abrir el nuevo, en ese orden:

```sql
begin;
update dex set hasta = date '2026-09-30'
 where producto = 'soja' and hasta = date '2999-12-31';
insert into dex (producto, desde, hasta, dex, nota)
 values ('soja', date '2026-10-01', date '2999-12-31', 0.20, 'Decreto NNN/2026');
commit;
```

Esas tres llevan un `EXCLUDE USING gist` que **prohíbe el solapamiento**. Sin él, un tramo mal
cargado haría que un mes matchee dos filas y el PRP se duplique en silencio; con él, el error
salta al escribir y no al leer. Si el `insert` de arriba falla, es porque el `update` no se hizo.

#### `dex`: los tramos que no son porcentajes redondos

De **2018-09 a 2019-11** rigió el derecho **fijo en pesos por dólar exportado** (Decreto
793/2018), no una alícuota. La tabla guarda el **equivalente ad valorem** ya calculado, que se
mueve todos los meses con el tipo de cambio y el precio FOB. Por eso ese tramo tiene una fila por
mes con valores como `0.28365379632`. No son un error de carga.

#### `tc_granos`: por qué es una tabla de excepciones

El tipo de cambio del PRP es el **A3500 del BCRA promediado por mes**. `tc_granos` sólo tiene los
meses en que **no** lo es, y `tc_granos_mensual` resuelve cuál se aplica (columna `motivo`):

| `motivo` | Período | Por qué |
|---|---|---|
| `convertibilidad` | 1993-01 → 2002-02 | el A3500 arranca el **2002-03-04**: antes no hay serie |
| `dolar_exportador` | 2022-09 → 2024-12 | programas de incremento exportador (dólar soja/agro a 200, 300 y 340 $/USD) y el blend 80/20 oficial-CCL |
| `a3500` | el resto | promedio mensual del A3500 |

Son **133 filas = 133 meses pisados** (110 de convertibilidad + 23 de dólar exportador); los
otros ~272 meses de la serie salen del A3500 y no tienen fila. Desde **2025-01** el A3500 vuelve
a ser el tipo de cambio efectivo, así que de ahí en adelante la tabla está vacía. Si mañana
vuelve un régimen diferencial, se agregan filas ahí y ninguna vista cambia.

### El deflactor: `deflactores`, no `indices_inflacion`

`ipc_base_1993` es `public.deflactores` con `deflactor = 'ipc_largo'` **reescalado a base
1993 = 1** (dividido por el promedio de los doce meses de 1993). La elección no es de gusto:

| Candidata | Cobertura | Veredicto |
|---|---|---|
| `deflactores` (`ipc_largo`) | **1990-01 → 2026-12** | la única que cubre el PRP entero |
| `indices_inflacion` | 2016-12 → 2026-04 | arranca 24 años tarde y además viene desactualizada |

**Los meses sin IPC observado usan la proyección, igual que el resto del repo.** `ipc_base_1993`
lee `deflactores` entero —observado y proyectado— y arrastra la columna `origen`, que llega hasta
`granos_prp` y `granos_prp_combinado` como `deflactor_origen`. Es la misma técnica de
`etl_datos_gob_real`. `deflactor_origen = 'proyectado'` marca los meses cuyo IPC todavía no
publicó INDEC: **esos PRP se revisan** y no hay que presentarlos como firmes.

```sql
-- Cuánto del PRP está apoyado en IPC estimado
select deflactor_origen, count(*), min(fecha), max(fecha)
from granos_prp_combinado group by 1;
```

> **Pero acá la base es FIJA, no móvil, y eso cambia el alcance de la revisión.** `datos_gob`
> reexpresa cada serie en la moneda de su último mes observado, así que cuando INDEC publica se
> mueve el mes base y **se revisa toda la historia de la serie**. El PRP está anclado a 1993 = 1
> y ese ancla no se mueve nunca: cuando un mes proyectado pasa a publicado **sólo cambia ese mes**.
> La revisión es local, no global.

`deflactores` lo mantiene **otro repo** (`downloaders_viejos/downloader`); para chequear frescura:

```sql
select max(fecha) from deflactores where deflactor = 'ipc_largo' and origen = 'publicado';
```

### Carga histórica: leer esto antes de correrla

La API de MAGyP **no tiene endpoint de rango**: un request por fecha. El histórico completo
(1993-01 → hoy) son **~8.600 días hábiles**. Dos cosas bajan ese número a ~3.300:

```bash
python -m etl fob_granos load-history --desde-precios-fob   # SQL, sin un solo request
python -m etl fob_granos load-history --solo-faltantes      # sólo lo que falta de verdad
python -m etl fob_granos load-history --desde 2020-01-01    # por tramos
python -m etl fob_granos                                    # corrida diaria (incremental)
```

**1. `public.precios_fob` ya tiene 1993-01-04 → 2017-04-19.** Es un volcado previo de esta misma
API y `--desde-precios-fob` lo importa por SQL. Coincide con la API: sobre las filas en común,
419 comparadas y **cero** con valor distinto. Aporta ~20.200 filas y ahorra ~5.000 requests. Dos
límites que hay que conocer:

- **Tiene un agujero de 2008 a 2011**: 2009 y 2010 enteros, 149 días de 2008, 218 de 2011 y ~35
  de más en 2004. **No es de la fuente** —la API responde esas fechas sin problema— así que hay
  que taparlo por API. Son ~940 días.
- **Recorta la curva forward**: en varios tramos guarda sólo la ventana de embarque más cercana.
  Eso **no afecta al PRP** (la ventana spot es justamente la más cercana y está presente en el
  98,2 % de los pares fecha-posición; el resto lo cubre el fallback), pero las filas importadas
  no traen el resto de la curva.

**2. `etl_fob_granos_sin_dato` registra los días que la fuente declaró vacíos.** Son ~1.200 entre
1993 y 2017 —feriados, sobre todo— y sin este registro cada `--solo-faltantes` los vuelve a
pedir: 80 minutos de requests que no traen nada. El ETL lo va llenando solo cada vez que la API
contesta con la lista vacía, y el schema siembra 263 días inferidos de `precios_fob` (validados
contra la API con una muestra al azar de 10, los 10 vacíos y los 10 feriados reconocibles).
`--desde`/`--hasta` sin `--solo-faltantes` ignora este registro, por si hay que volver a preguntar.

La pausa por defecto es **4 segundos** (~0,22 req/s) y sale de una cuenta, no de una corazonada:
el bloqueo del 02/08/2026 se disparó con ~1,8 req/s sostenidos, duró 4 horas y se llevó puesto el
dominio entero para los **cinco** ETLs que salen de esta misma IP contra `magyp.gob.ar`
(`granos`, `aves`, `bovinos`, `leche`, `compras_granos`). Correr el backfill por tramos y de
noche; es reanudable y re-correrlo siempre es seguro.

> El host canónico de la API (`monitorsiogranos.magyp.gob.ar`) devuelve **403 a todo**. La propia
> documentación de MAGyP avisa que hay que usar el espejo bajo `www.magyp.gob.ar`, que es el que
> usa el ETL — y es el mismo host que comparten los otros cinco.

> **La fuente devuelve de a ratos un 200 con el cuerpo vacío.** No es un día sin cotización: el
> mismo pedido repetido trae los datos (visto con 13/11/2007, que a la segunda trajo sus 145
> filas). `etl.core.http` no lo cubre —no hay status reintentable que mirar y `raise_for_status()`
> lo deja pasar— así que `source.get_dia` reintenta dos veces por su cuenta. Sin eso, un backfill
> de miles de días se llena de agujeros intermitentes que sólo se ven al comparar.

### Cómo se validó contra la planilla

Con la serie **completa** —1993-01-04 a 2026-09-10, cero días faltantes— se comparó columna por
columna contra `TCR Granos.xlsx`. Sobre los meses con 15 días cotizados o más: **1.613 pares
producto-mes y 404 meses de PRP combinado**.

| Columna | Resultado |
|---|---|
| `dex` | **exacto en los 1.613**, 100 % |
| `fob_usd` | **96,2 % dentro de ±0,5 %**, 97,3 % dentro de ±1 %, 98,1 % dentro de ±3 % |
| `tc` | **23 de los 34 años coinciden en todos sus meses**; los desvíos se concentran (ver abajo) |
| `prp_combinado` | mediana de \|dif\| **1,11 %**; 82 % dentro de ±3 %, **96 % dentro de ±5 %** |

De los 15 meses con mayor desvío del PRP combinado, **11 son de 2017** —el bug del tipo de
cambio de la planilla, donde nuestro número es el bueno—, 2 de 2007 y 2 de 2002.

**Dónde se aparta el tipo de cambio, y por qué.** Sólo tres tramos:

| Tramo | Mediana | Qué pasa |
|---|---|---|
| 2017 | 10,1 % | **bug de la planilla**: su columna es el A3500 corrido 9 meses. No se replicó |
| 2002 | 1,1 % | salida de la convertibilidad: usó otra fuente mientras el A3500 recién arrancaba |
| 2003-2011 | 0,6 % | sesgo chico **sin explicar**: no es la forma de promediar (promediar por días calendario en vez de por ruedas lo deja igual, 0,555 % contra 0,561 %), así que es otra serie de tipo de cambio |

El resto de los años coincide exacto. El residuo de 2003-2011 se deja anotado y no se corrige:
son 0,6 % contra el 1,3-4,5 % que aporta el deflactor, así que no cambia ninguna conclusión y
"arreglarlo" sería copiar una serie que no sabemos cuál es.

**La prueba fuerte del FOB.** La planilla carga el FOB **a mano y redondeado a entero** en 1.198
de sus 1.211 celdas; en 13 lo deja calculado. Una de esas trece es **soja marzo-2016 = 332,48**, y
`etl_fob_granos_mensual` devuelve **332,4761904…** para ese mes. Al centavo. Eso valida a la vez
la posición arancelaria elegida, el criterio de ventana spot y el promedio mensual: las tres
cosas tendrían que estar bien simultáneamente para dar ese número.

**De dónde sale el residuo que queda.** Tres fuentes, todas identificadas:

1. **El deflactor (siempre presente, -0,07 % a +4,5 %). Es la fuente dominante.** La planilla trae el IPC de un libro
   externo (`IPC99ON (real)`); nosotros usamos `deflactores`/`ipc_largo`. Son dos empalmes
   distintos del mismo índice y difieren sobre todo en los años de la intervención del INDEC
   (+4,5 % en 2007-2008, ~1,3-1,8 % de 2016 en adelante). Como el PRP divide por el IPC, el
   desvío pasa con signo invertido y **explica por sí solo casi todo el residuo**.
2. **El redondeo del FOB de la planilla (±0,5 %).** Ver arriba.
3. **Dos meses con el TC de otra fuente.** En 2002-03..2002-07 la planilla usa un tipo de cambio
   1 % a 6 % por encima del A3500 (la salida de la convertibilidad, con el A3500 recién
   arrancando el 2002-03-04), y hay blips de ~1 % en 2004-01 y 2008-05/06. **No se overridearon**:
   `tc_granos` es para **regímenes**, no para perseguir decimales.

**Lo que NO se replicó: el tipo de cambio de 2017 de la planilla está mal.** Su columna D coincide
exacto con el A3500 en 1993-2016, 2018-2022 y 2025-2026, pero los doce meses de **2017** son esa
misma serie **corrida 9 meses**: enero-2017 muestra el A3500 de abril-2016 y diciembre-2017 el de
marzo-2017. Es un copy-paste con offset —un desvío que empieza en enero y termina en diciembre no
es un régimen económico— y acá se usa el A3500 real. Por eso el PRP de 2017 queda **~10 % por
encima** del de la planilla: es corrección, no discrepancia.

**El trigo de 2018, 2019 y 2020 no sale de esta fuente.** Es la única divergencia sistemática
que quedó, y está acotada al año exacto. Desvío mediano del FOB contra la planilla, por grano y
año (sólo meses con 15 días cotizados o más):

| año | soja | **trigo** | maíz | girasol |
|---|---|---|---|---|
| 2014-2017 | 0,08 % | **0,06-0,11 %** | 0,13 % | 0,00 % |
| **2018** | 0,10 % | **3,53 %** | 0,97 % | 0,00 % |
| **2019** | 0,08 % | **3,31 %** | 0,22 % | 0,00 % |
| **2020** | 0,07 % | **6,62 %** | 0,22 % | 0,00 % |
| 2021-2026 | 0,08 % | **0,12-0,32 %** | 0,18 % | 0,00-0,13 % |

Antes de 2018 y después de 2020 el trigo coincide **a la décima de punto**, igual que los otros
tres granos en toda la serie. En el medio hay 24 meses con desvíos de hasta 18 %, y para los dos
lados: +18,0 % en agosto-2020 y -12,1 % en noviembre-2019.

**No es un corrimiento de fechas**, que era la sospecha razonable después del bug del tipo de
cambio de 2017. El test lo descarta: en el tramo sano (2010-2017) la planilla coincide con
nuestro valor del MISMO mes con un error mediano de **0,09 %**, y desplazarla un mes lo lleva a
2,6 %. En 2018-2020 el mínimo sigue estando en desplazamiento cero, pero vale **4,46 %** —
cincuenta veces peor—. La fecha está bien; lo que cambió durante esos tres años es de dónde salió
el número.

Acá se deja **el valor calculado desde la API de MAGyP**, que es el que la propia planilla
reproduce en los otros 30 años. `fob_granos_override` existe por si algún mes hace falta pisarlo,
pero hoy está **vacía**.

## `cot` (Commitments of Traders, CFTC) — cómo consumirlo

La posición especulativa en los tres granos de Chicago: **soja**, **maíz** y **trigo SRW**.
Semanal, con corte los **martes**; la CFTC publica los **viernes 15:30 ET**.

Es un dataset **semanal** y vive en el carril aparte, junto con `compras_granos`: `date` es la
fecha de corte del reporte y no aparece en `series_actual` ni en `series_diarias_actual`.

| Tabla / vista | Qué trae |
|---|---|
| `etl_cot` | append-only, valores **crudos** tal como los publica la CFTC |
| `etl_cot_actual` | último snapshot por (contrato, categoria, tipo, fecha), todavía crudo |
| **`etl_cot_neto`** | **lo que querés consumir**: posición neta, todo en contratos, homogéneo |

### Dos series que NO son la misma, y no se empalman

| `categoria` | Reporte | Desde | Qué mide |
|---|---|---|---|
| `managed_money` | Disaggregated | **2006-06-13** | fondos de gestión activa: CTAs, hedge funds de commodities |
| `non_commercial` | Legacy | **1986-01-15** | categoría **más amplia**: incluye a los managed money **más** otros especuladores reportables |

`managed_money` es la que sigue el mercado y la que se cita como "los fondos están netos
largos X contratos". Pero **no existe antes de 2006**: el reporte Disaggregated arranca ahí.
`non_commercial` es el único dato especulativo que hay para los veinte años anteriores.

Las dos están completas y **sin pegar**. Empalmarlas es una decisión de análisis —hay un salto
conceptual en 2006-06— y se toma al consumir, no en el ETL.

### Y dos variantes de cada una

| `tipo` | Qué incluye |
|---|---|
| `futures` | sólo futuros. **Es la serie que se cita habitualmente** |
| `futures_options` | futuros más opciones en equivalente futuro (delta-adjusted) |

El interés abierto los distingue de un vistazo: soja al 2026-09-08 da **1.070.401** contratos en
`futures` y **1.378.220** en `futures_options`.

> Un chequeo de integridad que sale gratis: para un mismo `tipo` y fecha, el `interes_abierto` es
> **idéntico** entre `managed_money` y `non_commercial`. Es el mismo contrato visto con dos
> particiones distintas. Si alguna vez difieren, algo se rompió en la ingesta.

### Cómo se consulta

```sql
-- Posición neta de los fondos, última semana
select contrato, largo, corto, neto, neto_pct_oi
from etl_cot_neto
where categoria = 'managed_money' and tipo = 'futures'
  and date = (select max(date) from etl_cot_neto)
order by contrato;
```

```
 contrato  | largo  | corto | neto    | neto_pct_oi
-----------+--------+-------+---------+-------------
 maiz      | 491034 | 76575 | +414459 |       +23.0
 soja      | 293215 | 35957 | +257258 |       +24.0
 trigo_srw | 100506 | 95633 |   +4873 |        +1.0
```

```sql
-- La serie larga de un grano (40 años), ya homogénea
select date, neto, neto_pct_oi
from etl_cot_neto
where contrato = 'soja' and categoria = 'non_commercial' and tipo = 'futures'
order by date;
```

**Usá `neto_pct_oi` para comparar entre granos o a lo largo de décadas.** El contrato de soja
pasó de ~96.000 contratos de interés abierto promedio en 1986-89 a ~911.000 hoy: un neto de
50.000 no significa lo mismo en 1990 que ahora. Los niveles en contratos sirven para leer la
semana; los porcentajes, para leer la historia.

> **El `spreading` NO entra en el neto**, a propósito. Son posiciones largas y cortas
> simultáneas en distintos vencimientos: por construcción no expresan dirección.

### Tres discontinuidades que hay que conocer

**1. La unidad cambia en 1998, y la vista ya lo corrige.** Hasta 1997 la CFTC reportaba los
granos en **miles de bushels**; desde el **1998-01-06**, en **contratos de 5.000 bushels**.
`etl_cot_neto` divide por 5 las 1.749 filas anteriores a esa fecha; `unidad_origen` y
`divisor_aplicado` marcan cuáles, y `etl_cot_actual` conserva el crudo.

Es una inferencia nuestra, no una conversión documentada por la CFTC, pero está **medida**: al
cruzar el 1998-01-06 el interés abierto de los **cuatro** contratos de granos del archivo cae en
un factor de 4,65 a 5,51 (trigo CBOT 4,65 · trigo Kansas 5,51 · maíz 4,70 · soja 5,12), mientras
que contratos que **no** se miden en bushels no se mueven: boneless beef trimmings 1,05 y
electricidad CA-OR 0,75. Si fuera un cambio general de unidad de reporte, esos dos también
saltarían. Con el divisor aplicado el interés abierto de soja pasa de 138.168 el 1997-12-30 a
133.992 el 1998-01-06 —3 %, una semana normal— y el promedio por lustro crece monótono sin
escalón.

**2. El código de contrato también cambia en 1998.** `005601` → `005602` (soja), `002601` →
`002602` (maíz), `001601` → `001602` (trigo). La ingesta los unifica bajo el mismo `contrato` y
el corte es limpio —último dato del código viejo 1997-12-30, primero del nuevo 1998-01-06, sin
ninguna fecha en común—. La columna `codigo_cftc` guarda de cuál vino cada fila.

**3. La frecuencia cambia, y esta no la corrige nadie.** El COT era **quincenal hasta 1991** (24
observaciones por año), 1992 es de transición (31) y recién **desde 1993 es semanal**. Calcular
variaciones semanales o medias móviles de N semanas sobre el tramo viejo da cualquier cosa:

```sql
-- Cuántas observaciones hay por año, antes de asumir que son 52
select extract(year from date)::int as anio, count(*) as obs
from etl_cot_neto
where contrato = 'soja' and categoria = 'non_commercial' and tipo = 'futures'
group by 1 order by 1;
```

### Cargarlo y mantenerlo

```bash
python -m etl cot load-history     # 1986 -> hoy, los cuatro origenes, ~44 zips
python -m etl cot                  # corrida semanal: 4 requests a la API Socrata
```

**Dos caminos que no son intercambiables.** `run` va por la **API Socrata** y `load-history` por
los **zips anuales**, y no es redundancia: la API **no tiene todo el histórico**. Legacy arranca
en 1998-01-06 y Disaggregated en 2006-06-13, así que el tramo **1986-1997** —los doce años que
hacen que la serie larga valga la pena— existe sólo en los archivos.

Que los dos caminos coinciden está verificado: correr `run` sobre lo que ya cargó `load-history`
reporta `sin_cambios` en todas las filas comunes.

A diferencia de MAGyP, acá **no hay que cuidar el ritmo**: la CFTC sirve desde un CDN del
gobierno de EEUU y no corta por volumen. El backfill entero son ~200 MB y termina en minutos.

> **La CFTC revisa reportes ya publicados.** Por eso la tabla es append-only y `run` re-lee 8
> semanas hacia atrás por defecto: releer es barato y es lo único que hace entrar la revisión.
> Para cazar revisiones viejas está `load-history --revisar`, que es lento a propósito.

> **Y a veces deja de publicar.** Los feriados de EEUU corren la publicación al lunes, y un
> cierre de gobierno la suspende por semanas: en 2018-2019 el COT estuvo cinco semanas sin salir
> y después publicó todo junto. Por eso `dias_max_dato` es 14 y no 7.
