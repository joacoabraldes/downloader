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
select dataset, ultimo_dato, dias_dato, dias_max_dato
from etl_control_salud where estado_dato <> 'ok';
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

`DATO_VIEJO` **no implica** que haya algo que arreglar. Lo más común es que el organismo se haya
atrasado. Lo que sí amerita mirarlo es el caso silencioso: la fuente publicó, pero cambió el
formato y el parser lo está ignorando sin lanzar excepción. Ese caso da `estado = ok` con
`estado_dato = DATO_VIEJO`, y es exactamente el que antes no se veía desde ninguna vista.

## Columnas

| Columna | Significado |
|---|---|
| `dataset` | Nombre del ETL. Aparecen los 14 siempre, hayan corrido o no. |
| `estado` | Salud del **proceso**: `ok`, `FALLA`, `SIN_CORRER` o `NUNCA_CORRIO`. |
| `estado_ultima_corrida` | Resultado de la última ejecución: `ok` o `falla`. Vacío si nunca corrió. |
| `ultima_corrida` | Fecha y hora en que terminó la última ejecución. |
| `horas_desde` | Horas transcurridas desde entonces. |
| `horas_max` | Máximo de horas que puede pasar sin correr sin que sea un problema. |
| `ultimo_dato` | Período más reciente cargado en ese dataset después de esa corrida. |
| `fallas` | Detalle de los errores. Vacío cuando la corrida fue exitosa. |
| `dias_dato` | Días transcurridos desde `ultimo_dato` hasta hoy. |
| `dias_max_dato` | Edad máxima que puede tener `ultimo_dato` sin que sea un problema. |
| `estado_dato` | Frescura del **dato**: `ok`, `DATO_VIEJO` o `SIN_DATO`. |

## Por qué `horas_max` cambia según el dataset

Cada ETL corre en la ventana del mes en que su fuente publica, no todos los días. El umbral de
"hace demasiado que no corre" tiene que respetar esa frecuencia:

| Dataset | Corre | `horas_max` |
|---|---|---|
| `demanda_energia`, `automotriz`, `bovinos`, `datos_gob` | todos los días | 80 h (cubre el fin de semana) |
| `reservas_pasivos` | lunes a viernes | 80 h (cubre el fin de semana) |
| `compras_granos` | lunes a viernes | 80 h (cubre el fin de semana) |
| `acero` | días 15 al 10 del mes siguiente | 130 h (~5 días) |
| `leche` | días 20 al 10 del mes siguiente | 260 h (~11 días) |
| `granos` | días 18 al 31 | 450 h (~19 días) |
| `icc` | días 17 al 31 | 470 h (~20 días) |
| `aves` | ventanas de fin de mes | 500 h (~21 días) |
| `cemento`, `patentamientos` | días 1 al 10 | 530 h (~22 días) |
| `icg` | días 22 al 31 | 580 h (~24 días) |

Es decir: que `cemento` lleve 20 días sin correr es normal, porque su ventana es del 1 al 10.
Que `demanda_energia` lleve cinco días sin correr no lo es.

**Por qué los "diarios" tienen 80 h y no 26.** La VM del servidor está apagada de 22:00 a 04:45 y
todo el fin de semana (viernes 22:00 a lunes 04:45), así que un cron diario en la práctica corre
de lunes a viernes. Entre la corrida del viernes y la del lunes pasan ~72 h sin que nada esté
roto: con un umbral de 26 h, `demanda_energia` daba un `SIN_CORRER` falso todos los lunes a la
mañana. Las 80 h dejan margen sobre ese hueco de fin de semana.

`automotriz` está en ese grupo desde agosto-2026: ADEFA no tiene fecha de publicación previsible
y julio-2026 salió después del día 10, con lo que la ventana 1-10 lo perdió y el dato hubiera
entrado recién tres semanas más tarde. Ahora corre todos los días, igual que `demanda_energia`.

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
| `reservas_pasivos` | 2-4 días (día hábil anterior) | 1 día hábil | 8 |
| `compras_granos` | 7-11 días | 7 días | 25 |
| `datos_gob` | variable (14 series) | 1 mes | 75 |
| `patentamientos`, `cemento`, `automotriz` | 31-37 días | 1 mes | 80 |
| `icc`, `icg` | ~24 días (publican **dentro** del mes) | 1 mes | 70 |
| `granos`, `leche`, `bovinos` | 42-50 días | 1 mes | 95 |
| `acero`, `aves`, `demanda_energia` | 60 días | 1 mes | 105 |

> En `reservas_pasivos` y `compras_granos` las dos lecturas coinciden, porque su `date` **no** es
> el primer día de un período: es el día hábil y la fecha de corte respectivamente. La distinción
> sólo muerde en las series mensuales.

La columna del medio se midió con `min(ingested_at)` por fecha en cada tabla, descartando los lotes
del backfill inicial (se reconocen porque cientos de fechas comparten el mismo `ingested_at`; sin
descartarlos, lo "observado" es la fecha del backfill y no significa nada).

Dos límites que conviene tener presentes:

- **`datos_gob` mide el corte total, no cada serie.** `ultimo_dato` es el máximo sobre las 14
  series, así que avanza en cuanto publica la más rápida. Una serie individual congelada no se
  ve acá.
- **Los umbrales son deliberadamente generosos.** Una alerta que grita al pedo se termina
  ignorando, y entonces no sirve para nada. Se pueden ajustar cuando haya varios meses de
  observación incremental real.

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
dataset, hay que actualizar su `horas_max` en `etl/schema_control.sql`; de lo contrario ese
dataset queda con un umbral que ya no corresponde y puede dar un `SIN_CORRER` falso o, peor,
dejar de avisar.

Lo mismo vale para `dias_max_dato`, pero el disparador es otro: no cambia con el cron, cambia
cuando **la fuente** mueve su calendario de publicación. Si un organismo empieza a publicar más
tarde de forma sostenida, el umbral viejo va a dar `DATO_VIEJO` todos los meses hasta que se
ajuste. Los dos umbrales se editan en el mismo bloque `esperado` de `etl/schema_control.sql`.

Para aplicar cualquier cambio de umbrales:

```bash
python -m etl init-db          # la vista es `create or replace`, es idempotente
```

> Al agregar columnas nuevas a `etl_control_salud`, van **al final** del `select`. Postgres sólo
> permite agregar columnas al final en un `create or replace view`; insertarlas en el medio
> obliga a un `drop view` y a recrear todo lo que dependa de ella.
