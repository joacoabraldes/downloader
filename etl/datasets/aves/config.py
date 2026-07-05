"""Config de la tabla etl_aves (formato long) para el núcleo genérico (etl.core.db).

Hoy una sola serie (`faena`, miles de cabezas), pero la tabla queda lista para sumar las
otras del Excel de indicadores (producción, exportaciones, consumo, etc.).
"""

TABLE = "etl_aves"
KEY_COLS = ["serie", "date"]
VALUE_COLS = ["valor"]
ACTUAL_VIEW = "etl_aves_actual"

# Serie principal (y única por ahora). Valor en miles de cabezas.
MAIN_SERIE = "faena"
SERIES = ["faena"]
