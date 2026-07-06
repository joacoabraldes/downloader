"""Config de la tabla etl_reservas_pasivos (formato long) para el núcleo genérico (etl.core.db).

Dataset DIARIO y multi-serie (37 series del archivo diar_bas.xls del BCRA). A diferencia de los
otros datasets, `serie` NO es un slug legible sino el `cd_serie` del BCRA (clave estable del
archivo; el código `bas*` NO es único). Los nombres legibles viven en la dimensión
`etl_reservas_pasivos_series` y la vista `etl_reservas_pasivos_actual` los une por JOIN.
"""

TABLE = "etl_reservas_pasivos"
KEY_COLS = ["serie", "date"]
VALUE_COLS = ["valor"]
ACTUAL_VIEW = "etl_reservas_pasivos_actual"

# Tabla catálogo/dimensión con los nombres legibles de cada serie.
CATALOG_TABLE = "etl_reservas_pasivos_series"

# Serie de referencia (reservas internacionales totales). No hay desest, así que no hay una
# "MAIN_SERIE" en el sentido de los otros datasets; se deja como puntero al total de reservas.
MAIN_SERIE = "246"
