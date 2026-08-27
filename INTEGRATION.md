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
| `hidrocarburos` | `etl_hidrocarburos` | `etl_hidrocarburos_actual` | `etl_hidrocarburos_desest` (sólo los 2 totales) |
| `escrituras_caba` | `etl_escrituras_caba` | `etl_escrituras_caba_actual` | `etl_escrituras_caba_desest` (sólo `compraventa`) |
| `icc` | `etl_icc` | `etl_icc_actual` | `etl_icc_desest` (vacía: no se desestacionaliza) |
| `icg` | `etl_icg` | `etl_icg_actual` | `etl_icg_desest` (vacía: no se desestacionaliza) |
| `datos_gob` | `etl_datos_gob` + dimensión `etl_datos_gob_series` | `etl_datos_gob_actual` (+ `_real` y `_completo`) | `etl_datos_gob_desest` (las 2 de ventas + las 2 de comercio exterior) |
| `comex` | `etl_comex` + dimensión `etl_comex_series` | `etl_comex_actual` | `etl_comex_desest` (sólo las 6 de cantidad) |
| `compras_granos` | `etl_compras_granos` | `etl_compras_granos_actual` | — (semanal, no se desestacionaliza) |

> Todas las tablas llevan prefijo **`etl_`**. El nombre de la tabla no siempre deriva directo
> del dataset (comando): `granos` → `etl_molienda_granos`, `cemento` → `etl_cemento_despacho`;
> el resto es `etl_<dataset>`. Consultá siempre las **vistas** `etl_<tabla>_actual` /
> `etl_<tabla>_desest` (o las unificadas), nunca la tabla append-only directamente.

## Vistas unificadas (recomendadas para consumo transversal)

Unen todos los datasets en una sola forma, agregando una columna `dataset`:

| Vista | Contenido |
|---|---|
| `series_actual` | serie **observada** de los 15 datasets mensuales (`dataset, serie, date, valor, estado, fuente, ingested_at`) |
| `series_desest` | serie **desestacionalizada** (`dataset, serie, date, valor, fuente, ingested_at, parametros`). `icc` e `icg` no aportan filas: se publican sin ajuste estacional. De `datos_gob` se ajustan las 2 de ventas y las 2 de comercio exterior (sobre su serie real); de `comex`, sólo las seis de cantidad |

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
| `hidrocarburos` | `petroleo`, `gas` (totales) + `<serie>_convencional`, `_shale`, `_tight` | `petroleo`, `gas` *(sólo los totales)* |
| `escrituras_caba` | `compraventa`, `monto`, `hipotecas`, `monto_medio`, `monto_medio_usd` | `compraventa` |
| `icc` | `nacional`, `capital`, `gba`, `interior`, `situacion_personal`, `situacion_macro`, `bienes_durables` | *(ninguna)* |
| `icg` | `icg` | *(ninguna)* |
| `datos_gob` | las 14: `isac`, `ipi_manufacturero`, `ipc_nacional`, `expo_total`, `impo_total`, `ventas_supermercados`, `ventas_centros_compras`, `ripte`, `smvm`, `indice_salarios_total`, `indice_salarios_registrado`, `indice_salarios_priv_registrado`, `indice_salarios_publico`, `indice_salarios_priv_no_registrado` | `ventas_supermercados`, `ventas_centros_compras`, `expo_total`, `impo_total` *(las 4 sobre la serie real)* |
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
| `dataset` | los 15, hayan corrido o no |
| `estado` | **proceso**: `ok` · `FALLA` · `SIN_CORRER` · `NUNCA_CORRIO` |
| `estado_ultima_corrida` | `ok` / `falla` de la última ejecución |
| `ultima_corrida` · `horas_desde` | cuándo terminó y hace cuánto |
| `horas_max` | hueco legítimo máximo según la ventana del cron |
| `ultimo_dato` | `max(date)` del dataset tras esa corrida |
| `fallas` | array con el detalle; `NULL` si anduvo |
| `dias_dato` · `dias_max_dato` | edad de `ultimo_dato` y su máximo tolerado |
| `estado_dato` | **dato**: `ok` · `DATO_VIEJO` · `SIN_DATO` |

`SIN_CORRER` significa que el cron dejó de disparar. `FALLA` significa que corrió y no pudo
traer el dato: ahí sí conviene ir al log, `/home/jmt/data/etls/<dataset>.log`.

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

14 series de `apis.datos.gob.ar/series` (INDEC y Secretaría de Trabajo). Es el único dataset
mensual con **star-schema**, porque sus series **no comparten unidad**: conviven índices,
dólares y pesos. La vista trae el nombre y la unidad ya unidos.

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
| `valor_desest` | X-13 sobre la serie **real** | si esa serie no se desestacionaliza |
| `mes_base` | Mes cuya moneda expresa `valor_real` | si `valor_real` es NULL |
| `deflactor_origen` | `publicado`, `proyectado` o `interpolado`: procedencia del índice de ese mes | si `valor_real` es NULL |
| `deflactor` | `ipc_largo` (pesos constantes) o `uscpi_mensual` (dólares constantes) | si `valor_real` es NULL |

Cada una tiene además su vista suelta: `etl_datos_gob_actual` (nominal),
`etl_datos_gob_real`, `etl_datos_gob_desest`.

### Las 14 series

| `serie` | Unidad | Desde | ¿real? | ¿desest? |
|---|---|---|---|---|
| `isac` | índice 2004=100 | 2012-01 | — | — |
| `ipi_manufacturero` | índice 2004=100 | 2016-01 | — | — |
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

> **Todavía sin calibrar, pero la referencia EXISTE.** A diferencia de `acero` o `cemento`,
> estos parámetros de X-13 (`mult` + `td` + `s3x5`) son los razonables para ventas minoristas,
> no los que reproducen un resultado oficial. La diferencia con `acero`/`cemento` es que ahí ya
> se calibró contra la planilla del organismo y acá no: INDEC **sí** publica su propia serie
> desestacionalizada a precios constantes, en la misma API, con ids
> `455.1_VENTAS_PREADA_0_M_44_44` (supermercados) y `458.1_VENTAS_TOTADA_0_M_52_56` (centros de
> compras), ambas índice 2017=100. Mientras no se calibre contra ellas, `valor_desest` es
> **nuestro** número, no el de INDEC: no citarlo como oficial.

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
`nafta` y `glp`, y `gasoil_mas_nafta` sale de **sumar las dos primeras ya ajustadas** (ajuste
indirecto): nafta pica en verano y gasoil en primavera, así que al sumarlas en crudo se cancelan
y el ajuste directo del agregado rompería la identidad `gasoil_mas_nafta = gasoil + nafta`. La
identidad se cumple **exacta** en los 199 meses; las filas derivadas se distinguen por
`parametros->>'metodo' = 'indirecto'`.

```sql
-- Consumo de surtidor sin estacionalidad
select date, valor from etl_ventas_combustibles_desest
where serie = 'gasoil_mas_nafta' order by date;
```

Cuánto trabaja el ajuste (|d11 − obs| / obs): `glp` 21,0% medio, `nafta` 4,0%, `gasoil` 3,6%,
`gasoil_mas_nafta` 2,9%. Vale la pena: diciembre→enero-2026 el crudo marca **−5,3%** y el ajustado
**+1,2%** — signo opuesto. Ojo con `glp`: es la de estacionalidad más fuerte (amplitud 67%) pero
**ninguna** configuración de X-13 sale limpia (todas reportan estacionalidad móvil, y M3 y M5
quedan fuera de rango). Q=0,77 es usable, pero es la de menor calidad de las tres.

> El último mes que publica la fuente aparece **con 0 en todos los productos** antes de estar
> listo. El ETL descarta esos meses (un mes entero en cero es el placeholder, no un derrumbe del
> consumo), así que en la base no vas a verlo — pero si consultás el dashboard directo, sí.
