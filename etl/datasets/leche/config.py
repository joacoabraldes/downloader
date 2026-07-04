"""Config de la tabla leche (formato long) para el núcleo genérico (etl.core.db).

Una sola serie (`produccion`, litros por mes). La clave incluye `serie` por consistencia con
los otros datasets long (y por si se suman otras series de lechería más adelante).
"""

TABLE = "leche"
KEY_COLS = ["serie", "date"]
VALUE_COLS = ["valor"]
ACTUAL_VIEW = "leche_actual"

# Serie principal (y única por ahora). Valor en litros por mes (~9e8).
MAIN_SERIE = "produccion"
SERIES = ["produccion"]
