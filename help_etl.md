# Control de ejecución de los ETL

## Qué responde esta vista

`etl_control_salud` responde **dos** preguntas, y las mantiene separadas a propósito:

| Pregunta | Columna | Si se cae, ¿es problema nuestro? |
|---|---|---|
| ¿El ETL sigue vivo? | `estado` | **Sí.** El cron dejó de disparar o la corrida falló. |
| ¿La fuente sigue publicando? | `estado_dato` | **No.** El ETL corre bien; el organismo no publicó. |

Son dos urgencias distintas y por eso son dos columnas distintas. Un ETL puede correr impecable
todos los días y devolver `sin_cambios` durante un mes porque el INDEC todavía no publicó: eso
es `estado = ok` y `estado_dato = DATO_VIEJO`. Al revés también pasa: el cron muerto hace una
semana da `SIN_CORRER` aunque el dato que ya está cargado sea el último que existe.

La separación importa porque las tablas de datos son *append-only*: cuando una corrida no
encuentra valores nuevos, no escribe nada. Por eso la fecha de última escritura de una tabla
**no** indica cuándo corrió el ETL, sino cuándo cambió un valor por última vez. Un ETL detenido
hace meses se ve igual que uno que corre todos los días sin novedades.

## Chequeo rápido

```sql
-- ¿Hay algo roto de nuestro lado? Esto es lo que se mira primero.
select * from etl_control_salud where estado <> 'ok';

-- ¿Alguna fuente dejó de publicar? No es accionable, pero explica un dato que "no avanza".
select dataset, ultimo_dato, dias_dato, dias_max_dato, series_no_ok
from etl_control_salud where estado_dato <> 'ok';

-- Si la fila anterior es datos_gob: cuál de sus 15 series se atrasó, y por cuánto.
select serie, ultimo_dato, dias, dias_max_dato, estado_dato
from etl_datos_gob_salud where estado_dato <> 'ok';
```

**Si no devuelven filas, está todo en orden.** Cada fila devuelta es algo a revisar.

## Los cuatro estados de `estado` (el proceso)

| Estado | Qué significa | Qué hacer |
|---|---|---|
| `ok` | La última corrida terminó bien y dentro de su frecuencia esperada. | Nada. |
| `FALLA` | El ETL corrió pero no pudo traer el dato: fuente caída, archivo que no se pudo procesar, o un cálculo que falló. | Ver la columna `fallas` para el detalle, y el log del dataset. |
| `SIN_CORRER` | Pasó más tiempo del esperado sin ninguna ejecución. El programador de tareas dejó de dispararlo. | Revisar el cron del servidor. No es un problema de la fuente de datos. |
| `NUNCA_CORRIO` | No existe ningún registro de ejecución para ese dataset. | Dataset nuevo sin configurar, o el control nunca pudo escribir. |

`FALLA` y `SIN_CORRER` son problemas de naturaleza distinta: en el primero el proceso está vivo
y la fuente falló; en el segundo el proceso directamente no se ejecutó.

## Los tres estados de `estado_dato` (la frescura)

| Estado | Qué significa | Qué hacer |
|---|---|---|
| `ok` | La fuente publicó dentro del plazo esperado para ese dataset. | Nada. |
| `DATO_VIEJO` | `ultimo_dato` superó `dias_max_dato`: hace más de lo normal que la fuente no publica nada nuevo. | **No es un bug del ETL.** Verificar a mano si el organismo publicó y el parser no lo vio, o si directamente no publicó. |
| `SIN_DATO` | El dataset no tiene ninguna fila cargada. | Dataset nuevo sin backfill, o la carga nunca escribió. |

En `datos_gob` la frescura se mide **por serie** (ver [más abajo](#datos_gob-umbral-por-serie)):
`DATO_VIEJO` significa que **alguna** de sus 15 series superó su propio umbral, y la columna
`series_no_ok` dice cuáles.

`DATO_VIEJO` **no implica** que haya algo que arreglar. Lo más común es que el organismo se haya
atrasado. Lo que sí amerita mirarlo es el caso silencioso: la fuente publicó, pero cambió el
formato y el parser lo está ignorando sin lanzar excepción. Ese caso da `estado = ok` con
`estado_dato = DATO_VIEJO`, y es exactamente el que antes no se veía desde ninguna vista.

## Columnas

| Columna | Significado |
|---|---|
| `dataset` | Nombre del ETL. Aparecen los 22 siempre, hayan corrido o no. |
| `estado` | Salud del **proceso**: `ok`, `FALLA`, `SIN_CORRER` o `NUNCA_CORRIO`. |
| `estado_ultima_corrida` | Resultado de la última ejecución: `ok` o `falla`. Vacío si nunca corrió. |
| `ultima_corrida` | Fecha y hora en que terminó la última ejecución. |
| `horas_desde` | Horas transcurridas desde entonces. |
| `horas_max` | Máximo de horas que puede pasar sin correr sin que sea un problema. |
| `ultimo_dato` | Período más reciente cargado en ese dataset después de esa corrida. |
| `fallas` | Detalle de los errores. Vacío cuando la corrida fue exitosa. |
| `dias_dato` | Días transcurridos desde `ultimo_dato` hasta hoy. |
| `dias_max_dato` | Edad máxima que puede tener `ultimo_dato` sin que sea un problema. Vacío en `datos_gob`, que tiene un umbral por serie. |
| `estado_dato` | Frescura del **dato**: `ok`, `DATO_VIEJO` o `SIN_DATO`. |
| `series_no_ok` | Sólo en `datos_gob`: las series fuera de `ok`, como `{serie:ESTADO,...}`. Vacío si están todas bien. |

## Por qué `horas_max` cambia según el dataset

Cada ETL corre en la ventana del mes en que su fuente publica, no todos los días. El umbral de
"hace demasiado que no corre" tiene que respetar esa frecuencia:

| Dataset | Corre | `horas_max` |
|---|---|---|
| `demanda_energia`, `bovinos`, `datos_gob` | todos los días | 80 h (cubre el fin de semana) |
| `reservas_pasivos` | lunes a viernes | 80 h (cubre el fin de semana) |
| `compras_granos` | lunes a viernes | 80 h (cubre el fin de semana) |
| `fob_granos` | lunes a viernes | 80 h (cubre el fin de semana) |
| `estimaciones_agricolas` | lunes | 200 h (una semana + margen) |
| `estimaciones_semanal`, `estimaciones_mensual` | viernes | 200 h (una semana + margen) |
| `bcba_pas` | viernes | 200 h (una semana + margen) |
| `cot` | todos los días | 80 h (cubre el fin de semana) |
| `acero` | días 15 al 10 del mes siguiente | 130 h (~5 días) |
| `leche`, `hidrocarburos` | días 20 al 10 del mes siguiente | 260 h (~11 días) |
| `granos`, `comex` | días 18 al 31 | 450 h (~19 días) |
| `icc` | días 17 al 31 | 470 h (~20 días) |
| `aves` | ventanas de fin de mes | 500 h (~21 días) |
| `cemento`, `patentamientos`, `automotriz` | días 1 al 10 | 530 h (~22 días) |
| `icg`, `escrituras_caba` | días 22 al 31 | 580 h (~24 días) |

Es decir: que `cemento` lleve 20 días sin correr es normal, porque su ventana es del 1 al 10.
Que `demanda_energia` lleve cinco días sin correr no lo es.

**Por qué los "diarios" tienen 80 h y no 26.** La VM del servidor está apagada de 22:00 a 04:45 y
todo el fin de semana (viernes 22:00 a lunes 04:45), así que un cron diario en la práctica corre
de lunes a viernes. Entre la corrida del viernes y la del lunes pasan ~72 h sin que nada esté
roto: con un umbral de 26 h, `demanda_energia` daba un `SIN_CORRER` falso todos los lunes a la
mañana. Las 80 h dejan margen sobre ese hueco de fin de semana.

**`automotriz` volvió a la ventana 1-10 el 14-ago-2026, contra su propio historial.** Había
pasado a diario porque ADEFA no tiene fecha de publicación previsible y julio-2026 salió *después
del día 10*: esa misma ventana lo perdió y el dato hubiera entrado tres semanas más tarde. Con
1-10 ese escenario vuelve a estar sobre la mesa, y el respaldo por gacetillas de prensa **no lo
cubre**: adelanta el dato sólo si el ETL corre, y entre el 11 y el 31 no corre.

Por eso su `dias_max_dato` subió de 80 a 105 en el mismo cambio, no bajó: si ADEFA publica pasado
el día 10, `ultimo_dato` puede llegar a ~92 días antes de que lo reemplace el mes siguiente. Un
umbral de 80 daría `DATO_VIEJO` cada vez que ADEFA se atrasa, que es justamente el caso que esta
ventana vuelve más probable.

> Si vuelve a perderse un mes, la vuelta atrás es `10 12 * * *` en el cron **y** `horas_max` a 80
> en `etl/schema_control_salud.sql`, en el mismo movimiento.

Los dos índices de UTDT (`icc`, `icg`) son la excepción del cuadro: se publican **dentro del
mes de referencia**, no al mes siguiente. UTDT difunde el ICC un jueves (entre el 17 y el 24) y
el ICG un lunes (entre el 22 y el 28), según el cronograma que publica cada año. Por eso sus
ventanas caen en la segunda mitad del mes y no arrancan el día 1.

## De dónde sale `dias_max_dato`

```
dias_max_dato = edad del label al publicarse + un período de la serie + margen
```

Los dos términos son fáciles de errar, cada uno a su manera.

**Primer término: no es el rezago de la fuente.** `date` guarda el **primer día** del período, así
que cuando el dato se publica su label ya viene con el período entero encima. Con `automotriz`:

```
ADEFA publica junio el 04/07   ->  rezago REAL de la fuente = 4 días (junio cierra el 30/06)
pero ultimo_dato vale 06-01    ->  edad del label ese día   = 33 días (29 + 4)
```

Van los 33, no los 4, porque el umbral compara contra `ultimo_dato`. Confundirlos hace leer
"`acero`: 60 días" como "el INDEC tarda dos meses en publicar", cuando en realidad tarda ~30 días
desde que cierra el mes.

**Segundo término: `ultimo_dato` envejece.** No se queda quieto esperando. El junio de `aves`
aparece a fines de julio y recién lo reemplaza el julio a fines de agosto: en todo ese mes su edad
sigue creciendo y llega a ~91 días sin que pase nada malo. Un umbral igual a la edad del label
daría falsa alarma **todos los meses**.

| Dataset | Edad del label al publicarse | Período | `dias_max_dato` |
|---|---|---|---|
| `fob_granos` | 1 día (3 si el último hábil fue viernes) **(estimado, no medido)** | 1 día hábil | 6 |
| `cot` | 3-5 días (corte martes, publica viernes) **(estimado, no medido)** | 1 semana | 14 |
| `reservas_pasivos` | 2-6 días, +3 desde el cambio de horario del cron (ver nota) | 1 día hábil | 11 |
| `compras_granos` | 7-11 días | 7 días | 25 |
| `estimaciones_agricolas` | `ultimo_dato` = fecha del release; sin calendario (vistos 2023-10, 2026-03, 2026-08) **(estimado, no medido)** | irregular | 210 |
| `estimaciones_semanal` | `ultimo_dato` = fecha del último informe semanal (jueves) **(estimado, no medido)** | 1 semana | 21 |
| `estimaciones_mensual` | `ultimo_dato` = fecha del último informe mensual (mitad de mes) **(estimado, no medido)** | 1 mes | 45 |
| `bcba_pas` | `ultimo_dato` = "Datos al" del último release (miércoles; el PAS sale el jueves) **(estimado, no medido)** | 1 semana | 17 |
| `datos_gob` | por serie, 42-101 días (ver abajo) | 1 mes | por serie, 90-150 |
| `patentamientos`, `cemento` | 31-37 días | 1 mes | 80 |
| `automotriz` | 33 días, pero la ventana 1-10 puede atrasar la captura un mes entero | 1 mes | 105 |
| `icc`, `icg` | ~24 días (publican **dentro** del mes) | 1 mes | 70 |
| `granos`, `leche`, `bovinos`, `comex` | 42-50 días | 1 mes | 95 |
| `acero`, `aves`, `demanda_energia` | 60 días | 1 mes | 105 |
| `hidrocarburos` | ~50-56 días **(estimado, no medido)** | 1 mes | 105 |
| `escrituras_caba` | 51-56 días (medido sobre 120 informes) | 1 mes | 105 |

> **Nota sobre `hidrocarburos`.** Su umbral es el único **estimado y no medido**: al 2026-08 no
> hay corridas incrementales todavía y la única observación es que el dato de julio-2026 ya
> estaba disponible el 26-ago. Reajustar cuando haya varios meses de incremental real, igual que
> se hizo con `comex`.

> **Nota sobre `reservas_pasivos` (16/08/2026).** Su cron pasó de 19:15 + 20:30 a 10:00 + 16:30 por
> pedido explícito, sabiendo el costo: el BCRA sube `diar_bas.xls` a la tarde (~18:22), así que
> ninguna de las dos pasadas ve la publicación del día y el dato entra a la mañana siguiente. Eso
> le suma ~15 h de lag por día hábil y ~3 días cuando la publicación cae viernes, y por eso
> `dias_max_dato` subió de 8 a 11. Con 8 habría disparado `DATO_VIEJO` falso. Para revertir:
> volver a `15 19` / `30 20` en el crontab y `dias_max_dato` a 8.

> **Nota sobre `cot`.** La CFTC corta los martes y publica los viernes 15:30 ET, así que el lag
> normal del label es de 3 a 5 días. El umbral de 14 deja margen para dos cosas: los feriados de
> EEUU, que corren la publicación al lunes, y los cierres de gobierno, que la suspenden por
> semanas — en 2018-2019 el COT estuvo cinco semanas sin publicarse y después salió todo junto.

> **Nota sobre `fob_granos`.** MAGyP publica el precio FOB del día hábil ese mismo día y el ETL
> corre a la mañana siguiente, así que el lag normal es de 1 día y de 3 cuando el último hábil
> fue viernes. Umbral **estimado, no medido**: reajustar con corridas incrementales reales. Ojo
> con lo que NO detecta: `ultimo_dato` es el máximo sobre los cuatro granos, así que un grano
> individual congelado no dispara nada — sólo el corte total.

> En `reservas_pasivos`, `compras_granos` y `fob_granos` las dos lecturas coinciden, porque su
> `date` **no** es el primer día de un período: es el día hábil y la fecha de corte
> respectivamente. La distinción sólo muerde en las series mensuales.

La columna del medio se midió con `min(ingested_at)` por fecha en cada tabla, descartando los lotes
del backfill inicial (se reconocen porque cientos de fechas comparten el mismo `ingested_at`; sin
descartarlos, lo "observado" es la fecha del backfill y no significa nada). La excepción es
`comex`, que al 2026-08 sólo tiene el backfill: sus ~50 días salen del `Last Saved` de las
planillas del INDEC (junio-2026 quedó guardado el 20-jul). Reajustar cuando haya corridas
incrementales reales.

**Los umbrales son deliberadamente generosos.** Una alerta que grita al pedo se termina
ignorando, y entonces no sirve para nada. Se pueden ajustar cuando haya varios meses de
observación incremental real.

### `datos_gob`: umbral por serie

En `datos_gob` un umbral de dataset no sirve. Son 15 series de organismos distintos, y el
`max(date)` del dataset es ciego por dos motivos:

- `smvm` trae meses **futuros**: el salario mínimo se fija por decreto con meses de anticipación
  (hoy hasta 2027-04), así que ese máximo no envejece nunca y `DATO_VIEJO` no podía dispararse.
- Aun sin eso, avanza en cuanto publica la serie más rápida. Los cinco `indice_salarios_*`
  llegaron a estar 162 días parados con el control en `ok`.

Por eso cada serie tiene su propio `dias_max_dato`, declarado en `DIAS_MAX_DATO` de
`etl/datasets/datos_gob/config.py`. `run.py` lo copia a la dimensión `etl_datos_gob_series` en
cada corrida, y la vista `etl_datos_gob_salud` da una fila por serie con `ultimo_dato`, `dias`,
`dias_max_dato` y `estado_dato` (`ok`, `DATO_VIEJO`, `SIN_DATO` o `SIN_UMBRAL`, este último si a
la dimensión le falta el umbral). `etl_control_salud` pone la fila de `datos_gob` en `DATO_VIEJO`
si **cualquier** serie no está en `ok`.

La edad del label se midió el 2026-09-18 con `min(ingested_at)::date - date` de cada mes nuevo,
descartando el backfill del 2026-08-12. Es un mes de corridas incrementales, así que hay una o dos
observaciones por serie:

| Serie | Edad del label observada | `dias_max_dato` |
|---|---|---|
| `ipc_nacional` | 42-44 días (jul visto 14-ago, ago visto 12-sep) | 90 |
| `smvm` | 12 días; ago llegó a ~43 sin reemplazo (trae meses futuros) | 90 |
| `isac`, `ipi_manufacturero` | 70 días (jul visto 09-sep) | 115 |
| `expo_total`, `impo_total`, `saldo_total` | 48 días (ago visto 18-sep, el día del ICA, desde la planilla del INDEC) | 100 |
| `ripte` | 74-75 días | 120 |
| `ventas_supermercados`, `ventas_centros_compras` | 91 días | 140 |
| los 5 `indice_salarios_*` | 101 días, cota superior (el CSV se sumó como fuente ese día) | 150 |

El umbral sigue la misma cuenta: edad del label + 31 días de período + margen. La edad es la
que aparece **en la base** desde la fuente primaria de cada serie, no la del calendario del
organismo: la API de `apis.datos.gob.ar` puede ir semanas atrás del INDEC, y un umbral que no lo
tolere daría falsa alarma.

Comercio exterior se mide contra la planilla `balanmensual.xls` del INDEC, su fuente primaria
desde el 2026-09-18. El ICA sale a mitad del mes siguiente a las 16:00 y la corrida de las 17:30
lo levanta el mismo día; justo antes del ICA siguiente el último dato tiene ~80 días. 48 + 31 +
margen = 100. Con la API sola (julio visto 16-sep, 77 días) el umbral era 120. Bajarlo es a
propósito: si la planilla se cae varias semanas y las series viven de la API de respaldo, que va
~4 semanas atrás, también salta por frescura, además de la falla que ya registra la corrida.

Con `smvm` la edad es negativa mientras el último mes fijado esté en el futuro. La alarma salta
si, pasado el cronograma, no aparece el decreto siguiente.

## Detalle de una ejecución

Para ver el historial completo, incluidas las corridas anteriores:

```sql
select * from etl_control_ejecucion
where dataset = 'aves'
order by inicio desc
limit 20;
```

Además de lo anterior, esa tabla incluye la duración de cada corrida y los contadores de
registros leídos, nuevos y actualizados.

## Mantenimiento

Los umbrales de `horas_max` se derivan de las ventanas del cron. Si se cambia la ventana de un
dataset, hay que actualizar su `horas_max` en `etl/schema_control_salud.sql`; de lo contrario ese
dataset queda con un umbral que ya no corresponde y puede dar un `SIN_CORRER` falso o, peor,
dejar de avisar.

Lo mismo vale para `dias_max_dato`, pero el disparador es otro: no cambia con el cron, cambia
cuando **la fuente** mueve su calendario de publicación. Si un organismo empieza a publicar más
tarde de forma sostenida, el umbral viejo va a dar `DATO_VIEJO` todos los meses hasta que se
ajuste. Los dos umbrales se editan en el mismo bloque `esperado` de
`etl/schema_control_salud.sql`.

La excepción es `datos_gob`: sus umbrales son por serie y se editan en `DIAS_MAX_DATO` de
`etl/datasets/datos_gob/config.py`. Toman efecto en la próxima corrida del ETL, que los copia a la
dimensión, o antes con cualquier `init-db`, que también los sincroniza.

Cualquier `init-db`, aunque sea de un solo dataset, deja creada `etl_control_salud`: si falta la
vista por serie de datos_gob, aplica ese schema primero.

Para aplicar cualquier cambio de umbrales de `etl_control_salud`:

```bash
python -m etl init-db          # la vista es `create or replace`, es idempotente
```

`etl_control_salud` vive en `etl/schema_control_salud.sql`, separada de `etl/schema_control.sql`
(la tabla de corridas), porque lee `etl_datos_gob_salud` y ésa la crea el schema de `datos_gob`.
`init-db` aplica la tabla de corridas primero, después los datasets, y la vista de salud al
final. Si la base es nueva y se inicializa sin `datos_gob`, la vista se saltea con un aviso.

> Al agregar columnas nuevas a `etl_control_salud`, van **al final** del `select`. Postgres sólo
> permite agregar columnas al final en un `create or replace view`; insertarlas en el medio
> obliga a un `drop view` y a recrear todo lo que dependa de ella.
