"""Config de la tabla de molienda de granos (formato LONG) para el núcleo (etl.core.db).

Pasó de wide (1 fila/mes con 8 columnas) a long: 1 fila por (serie, date). Las series son
el `total` (= suma de los 7 granos) + los 7 granos. La desest X-13 corre solo sobre la serie
principal (`total`).
"""

TABLE = "molienda_granos"
KEY_COLS = ["serie", "date"]
VALUE_COLS = ["valor"]
ACTUAL_VIEW = "molienda_granos_actual"

# Series (orden estable). Coinciden con el CHECK de schema.sql.
SERIES = ["total", "soja", "girasol", "lino", "mani", "algodon", "cartamo", "canola"]
MAIN_SERIE = "total"

# Mapeo serie -> clave en el dict que producen source.py / load_history. El total viene como
# 'valor'; el resto coincide con el nombre de la serie (se usa SERIE_COL.get(serie, serie)).
SERIE_COL = {"total": "valor"}
