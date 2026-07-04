"""Config de la tabla patentamientos (formato long) para el núcleo genérico (etl.core.db).

Como automotriz, la clave incluye `serie`: hay una serie por categoría del mercado 4W,
cada una con su propia desestacionalización X-13.

De la Tabla 1 del informe SIOMAA ("Resumen del mercado") sólo guardamos las **unidades
del mes** por categoría. Las columnas derivadas del PDF (variaciones m/m, a/a y
acumulados) NO se guardan: son recalculables desde la serie observada.
"""

TABLE = "patentamientos"
KEY_COLS = ["serie", "date"]
VALUE_COLS = ["valor"]
ACTUAL_VIEW = "patentamientos_actual"

# Serie principal (para la ventana incremental y como headline del dataset).
MAIN_SERIE = "total_mercado"

# Mapeo etiqueta de la Tabla 1 (como aparece en el PDF) -> nombre de serie en la base.
# El orden importa para el matcheo por prefijo en source.py: las etiquetas más largas van
# primero (así "Autos + C.L." no matchea como "Autos"). SERIES conserva el orden estable.
LABEL_TO_SERIE = {
    "Autos + C.L. + C.P.": "autos_cl_cp",
    "Autos + C.L.": "autos_cl",
    "Comercial Liviano": "comercial_liviano",
    "Comercial Pesado": "comercial_pesado",
    "Otros Pesados": "otros_pesados",
    "Total Mercado": "total_mercado",
    "Autos": "autos",
}

# Series (orden estable). Coinciden con el CHECK de schema.sql y con el bloque
# [patentamientos] de series_desest.toml.
SERIES = ["total_mercado", "autos", "comercial_liviano", "comercial_pesado",
          "otros_pesados", "autos_cl", "autos_cl_cp"]
