"""Config de la tabla etl_comex (formato long) para el núcleo genérico (etl.core.db).

Como acero/automotriz, la clave incluye `serie`. El nombre de serie es un slug plano
`<flujo>_<indice>_<rubro>` (p.ej. `expo_cantidad_moa`) porque el cuadro de desestacionalización
(`etl/series_desest.toml`) selecciona por nombre de serie: las tres dimensiones no pueden vivir
en columnas separadas de la tabla de hechos. Sí viven desarmadas en la dimensión
`etl_comex_series`, que la vista `etl_comex_actual` une por JOIN para poder filtrar por rubro o
por índice sin parsear strings.
"""

TABLE = "etl_comex"
KEY_COLS = ["serie", "date"]
VALUE_COLS = ["valor"]
ACTUAL_VIEW = "etl_comex_actual"

# Tabla catálogo/dimensión: desarma el slug en flujo / índice / rubro y le pone nombre legible.
CATALOG_TABLE = "etl_comex_series"

# Base del índice. Va en el chequeo del parser: si INDEC rebasea (ya pasó: base 1993 -> 2004),
# los valores nuevos no son comparables con los que tenemos y hay que rehacer la serie, no
# apendear. Ver `source._check_base`.
BASE = "2004=100"

INDICES = ("valor", "precio", "cantidad")
# Rubros de la planilla de exportaciones. `general` es el "Nivel general" (el total).
RUBROS_EXPO = ("general", "primarios", "moa", "moi", "combustibles")
# La planilla de comex sólo abre importaciones a nivel general: INDEC no publica los grandes
# rubros de importación en esta serie mensual (los agrupa por uso económico, otro cuadro).
RUBROS_IMPO = ("general",)

SERIES = (
    [f"expo_{i}_{r}" for r in RUBROS_EXPO for i in INDICES]
    + [f"impo_{i}_{r}" for r in RUBROS_IMPO for i in INDICES]
)

# Serie de referencia del dataset (el total exportado en volumen).
MAIN_SERIE = "expo_cantidad_general"

# Series que se desestacionalizan, en el orden en que las exporta `python -m etl export comex`.
# La lista canónica —con los parámetros de X-13— vive en `etl/series_desest.toml`; ésta es sólo
# el orden de columnas del CSV.
DESEST_SERIES = [f"expo_cantidad_{r}" for r in RUBROS_EXPO] + ["impo_cantidad_general"]
