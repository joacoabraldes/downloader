"""Config de la tabla etl_acero (formato long) para el núcleo genérico (etl.core.db).

Como automotriz/patentamientos, la clave incluye `serie`. Hoy hay una sola serie
(`acero_crudo`), pero la tabla queda lista para sumar las otras del PDF (arrabio, esponja,
laminados, etc.) si en algún momento se decide ingestarlas.
"""

TABLE = "etl_acero"
KEY_COLS = ["serie", "date"]
VALUE_COLS = ["valor"]
ACTUAL_VIEW = "etl_acero_actual"

# Serie principal (y única por ahora). Valor en miles de toneladas.
MAIN_SERIE = "acero_crudo"
SERIES = ["acero_crudo"]

# Orden de las 8 columnas de datos de la Tabla del PDF (por si se amplía el alcance). Hoy
# sólo se ingesta `acero_crudo` (índice 3): arrabio, esponja y total son hierro primario;
# planos 1/2/total son laminados terminados en caliente; en_frio es hierro no laminado.
PDF_COLS = ["arrabio", "esponja", "hierro_primario_total", "acero_crudo",
            "laminados_planos_1", "laminados_planos_2", "laminados_total", "planos_en_frio"]
