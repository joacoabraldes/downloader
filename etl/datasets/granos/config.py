"""Config de la tabla de molienda de granos (formato LONG) para el núcleo (etl.core.db).

Pasó de wide (1 fila/mes con 8 columnas) a long: 1 fila por (serie, date). Las series son
el `total` (= suma de los 7 granos) + los 7 granos. La desest X-13 corre solo sobre la serie
principal (`total`).
"""

import datetime as dt

TABLE = "etl_molienda_granos"
KEY_COLS = ["serie", "date"]
VALUE_COLS = ["valor"]
ACTUAL_VIEW = "etl_molienda_granos_actual"

# Series (orden estable). Coinciden con el CHECK de schema.sql.
SERIES = ["total", "soja", "girasol", "lino", "mani", "algodon", "cartamo", "canola"]
MAIN_SERIE = "total"

# Mapeo serie -> clave en el dict que producen source.py / load_history. El total viene como
# 'valor'; el resto coincide con el nombre de la serie (se usa SERIE_COL.get(serie, serie)).
SERIE_COL = {"total": "valor"}

# Piso histórico. X-13 no puede con la serie completa desde 1965: la serie `total` (sin ceros
# pero larguísima, 737 meses) cuelga el binario, y `soja` no produce salida por los ceros del
# tramo viejo. Se recorta el input a 1993-01 en adelante (inclusive), único tramo que corre
# bien. Se filtra en los DOS caminos de ingesta (run.py = HTML, load_history.py = Excel) para
# que ninguna corrida vuelva a cargar lo anterior. La base ya fue recortada a este piso.
START_DATE = dt.date(1993, 1, 1)
