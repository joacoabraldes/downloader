"""Config de la tabla etl_compras_granos (formato long) para el núcleo genérico (etl.core.db).

Grano de la fila: (cultivo, cosecha, sector, metrica, date). `date` es la fecha de corte del
informe semanal. Se eligió long y no wide porque el set de métricas publicadas cambió dos veces
en la historia de la fuente (ver source.py): en wide, los tres formatos convivirían como columnas
mayormente NULL y cada cambio futuro de la fuente obligaría a un ALTER TABLE.
"""

TABLE = "etl_compras_granos"
KEY_COLS = ["cultivo", "cosecha", "sector", "metrica", "date"]
VALUE_COLS = ["valor"]
ACTUAL_VIEW = "etl_compras_granos_actual"

# Un solo estado para los dos caminos de ingesta (load-history y run) porque ambos leen la MISMA
# fuente: el HTML semanal. En los datasets mensuales estado=NULL significa "histórico de Excel" y
# la vista _actual lo prioriza sobre el provisorio del HTML; acá esa prioridad sería un bug —
# taparía una revisión posterior de MAGyP sobre una semana ya cargada. Con un único estado, la
# vista se queda siempre con el snapshot más reciente.
ESTADO = "definitivo"

# Cultivos (orden de las solapas de la fuente). Cebada existe recién desde 2017.
CULTIVOS = ["trigo", "maiz", "sorgo", "cebada_cervecera", "cebada_forrajera", "soja", "girasol"]

SECTORES = ["exportador", "industria", "total"]

# Métricas de los tres formatos históricos. Ninguna semana las trae todas: cada formato publica
# su subconjunto (ver source.METRICAS_POR_FORMATO en la docstring de source.py).
METRICAS = [
    "semanal",            # compras de la semana
    "total_comprado",     # compras acumuladas de la campaña (2017-18 lo titula "Total Acumulado")
    "precio_hecho",       # comprado con precio ya hecho (desde 2019)
    "a_fijar",            # comprado a fijar precio
    "fijado",             # de lo a fijar, ya fijado
    "saldo_a_fijar",      # a_fijar - fijado (desde 2019)
    "djve_acum",          # DJVE acumuladas (desde 2017)
    "embarque_estimado",  # embarque estimado acumulado del año comercial (solo 2005-2016)
    # OJO: la fuente sigue mostrando el encabezado "VENTAS" hasta 2015, pero con "SIN DATOS"
    # debajo. Los valores reales terminan en 2007/2008 (verificado sobre la base cargada).
    "ventas_potenciales",  # con datos solo 2005-2007
    "ventas_efectivas",    # con datos solo 2005-2008
    "compras_estimadas",   # bloque industria del formato viejo (2005-2016)
    "compras_declaradas",  # bloque industria del formato viejo (2005-2016)
]

# Primer año publicado por la fuente (2005 arranca en marzo, no en enero).
START_YEAR = 2005

UNIDAD = "miles de toneladas"
