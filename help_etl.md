# Control de ejecución de los ETL

## Qué responde esta vista

`etl_control_salud` responde **si el proceso corrió**, no si el dato está actualizado. Son dos
preguntas distintas y conviene no mezclarlas:

- **¿El dato está viejo?** Se responde mirando las tablas de datos.
- **¿El ETL sigue vivo?** Se responde con esta vista.

La distinción importa porque las tablas de datos son *append-only*: cuando una corrida no
encuentra valores nuevos, no escribe nada. Por eso la fecha de última escritura de una tabla
**no** indica cuándo corrió el ETL, sino cuándo cambió un valor por última vez. Un ETL detenido
hace meses se ve igual que uno que corre todos los días sin novedades.

## Chequeo rápido

```sql
select * from etl_control_salud where estado <> 'ok';
```

**Si no devuelve filas, está todo en orden.** Cada fila devuelta es un problema a revisar.

## Los cuatro estados

| Estado | Qué significa | Qué hacer |
|---|---|---|
| `ok` | La última corrida terminó bien y dentro de su frecuencia esperada. | Nada. |
| `FALLA` | El ETL corrió pero no pudo traer el dato: fuente caída, archivo que no se pudo procesar, o un cálculo que falló. | Ver la columna `fallas` para el detalle, y el log del dataset. |
| `SIN_CORRER` | Pasó más tiempo del esperado sin ninguna ejecución. El programador de tareas dejó de dispararlo. | Revisar el cron del servidor. No es un problema de la fuente de datos. |
| `NUNCA_CORRIO` | No existe ningún registro de ejecución para ese dataset. | Dataset nuevo sin configurar, o el control nunca pudo escribir. |

`FALLA` y `SIN_CORRER` son problemas de naturaleza distinta: en el primero el proceso está vivo
y la fuente falló; en el segundo el proceso directamente no se ejecutó.

## Columnas

| Columna | Significado |
|---|---|
| `dataset` | Nombre del ETL. Aparecen los 11 siempre, hayan corrido o no. |
| `estado` | El resumen: `ok`, `FALLA`, `SIN_CORRER` o `NUNCA_CORRIO`. |
| `estado_ultima_corrida` | Resultado de la última ejecución: `ok` o `falla`. Vacío si nunca corrió. |
| `ultima_corrida` | Fecha y hora en que terminó la última ejecución. |
| `horas_desde` | Horas transcurridas desde entonces. |
| `horas_max` | Máximo de horas que puede pasar sin correr sin que sea un problema. |
| `ultimo_dato` | Período más reciente cargado en ese dataset después de esa corrida. |
| `fallas` | Detalle de los errores. Vacío cuando la corrida fue exitosa. |

## Por qué `horas_max` cambia según el dataset

Cada ETL corre en la ventana del mes en que su fuente publica, no todos los días. El umbral de
"hace demasiado que no corre" tiene que respetar esa frecuencia:

| Dataset | Corre | `horas_max` |
|---|---|---|
| `demanda_energia` | todos los días | 26 h (~1 día) |
| `reservas_pasivos` | lunes a viernes | 80 h (cubre el fin de semana) |
| `compras_granos` | lunes a viernes | 80 h (cubre el fin de semana) |
| `leche` | días 20 al 10 del mes siguiente | 260 h (~11 días) |
| `granos` | días 18 al 31 | 450 h (~19 días) |
| `acero`, `aves`, `bovinos` | ventanas de fin de mes | 500 h (~21 días) |
| `cemento`, `automotriz`, `patentamientos` | días 1 al 10 | 530 h (~22 días) |

Es decir: que `cemento` lleve 20 días sin correr es normal, porque su ventana es del 1 al 10.
Que `demanda_energia` lleve 2 días sin correr no lo es.

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
