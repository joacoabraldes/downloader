"""Config de la tabla etl_bovinos (formato long) para el núcleo genérico (etl.core.db).

Hoy una sola serie (`produccion`, miles de toneladas res con hueso), pero la tabla queda lista
para sumar las otras del xls (faena en cabezas, % hembras, peso, etc.).
"""

TABLE = "etl_bovinos"
KEY_COLS = ["serie", "date"]
VALUE_COLS = ["valor"]
ACTUAL_VIEW = "etl_bovinos_actual"

# Serie principal (y única por ahora). Valor en miles de toneladas res con hueso.
MAIN_SERIE = "produccion"
SERIES = ["produccion"]
