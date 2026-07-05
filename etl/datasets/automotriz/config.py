"""Config de la tabla etl_automotriz (formato long) para el núcleo genérico (etl.core.db).

A diferencia de granos/cemento, la clave incluye `serie`: hay 3 series independientes
(produccion, ventas, expo), cada una con su propia desestacionalización X-13.
"""

TABLE = "etl_automotriz"
KEY_COLS = ["serie", "date"]
VALUE_COLS = ["valor"]
ACTUAL_VIEW = "etl_automotriz_actual"

# Series (orden estable). Coinciden con el CHECK de schema.sql y con los nombres de
# hoja de ind_automotriz.xlsx.
SERIES = ["produccion", "ventas", "expo"]
