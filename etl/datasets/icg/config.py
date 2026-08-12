"""Config de la tabla etl_icg (formato long) para el núcleo genérico (etl.core.db).

Índice de Confianza en el Gobierno (UTDT). Una sola serie, en escala 0-5.
"""

TABLE = "etl_icg"
KEY_COLS = ["serie", "date"]
VALUE_COLS = ["valor"]
ACTUAL_VIEW = "etl_icg_actual"

# Página "Descarga de datos" del ICG, de donde se resuelve el link al .xls en cada corrida.
ID_ITEM_MENU = 28756

# Serie principal (y única). Índice en escala 0-5.
MAIN_SERIE = "icg"
SERIES = ["icg"]
