"""Config de la tabla etl_icc (formato long) para el núcleo genérico (etl.core.db).

Índice de Confianza del Consumidor (UTDT). 7 series en la misma planilla: el índice nacional,
sus 3 aperturas geográficas y sus 3 subíndices. Todas en la misma escala (0-100).
"""

TABLE = "etl_icc"
KEY_COLS = ["serie", "date"]
VALUE_COLS = ["valor"]
ACTUAL_VIEW = "etl_icc_actual"

# Página "Serie Histórica ICC" de donde se resuelve el link al .xls en cada corrida.
ID_ITEM_MENU = 16458

# Orden estable. Coincide con el CHECK de schema.sql.
SERIES = ["nacional", "capital", "gba", "interior",
          "situacion_personal", "situacion_macro", "bienes_durables"]
