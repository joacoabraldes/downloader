"""Config de la tabla etl_demanda_energia (formato long) para el núcleo genérico (etl.core.db).

Todas las filas del cuadro 'DEMANDA' de CAMMESA se guardan como series. La serie principal
(la que se desestacionaliza y tiene histórico en energia.xlsx) es `no_residencial`.
Valores en GWh (la fuente CAMMESA está en MWh; source.py divide por 1000).
"""

TABLE = "etl_demanda_energia"
KEY_COLS = ["serie", "date"]
VALUE_COLS = ["valor"]
ACTUAL_VIEW = "etl_demanda_energia_actual"

# Serie principal: demanda no residencial total (= no residencial estacionalizada + no
# estacionalizada). Es la única con histórico profundo (energia.xlsx) y la que se desestacionaliza.
MAIN_SERIE = "no_residencial"

# Mapa etiqueta del cuadro (col A, en minúscula) -> nombre de serie. Se parsea POR ETIQUETA,
# no por número de fila: CAMMESA agregó 'MATE DISTRIBUIDOR' y corrió DEMANDA LOCAL una fila.
LABEL_TO_SERIE = {
    "demanda estacionalizada": "estacionalizada",
    "demanda residencial": "residencial",
    "demanda no residencial": "no_res_estacionalizada",  # parte no-res de la estacionalizada
    "demanda no estacionalizada": "no_estacionalizada",   # grandes usuarios (todos no-res)
    "gudi": "gudi",
    "gume": "gume",
    "guma": "guma",
    "mate distribuidor": "mate_distribuidor",
    "demanda local": "local",
}

# Componentes de la serie derivada `no_residencial` (suma).
NO_RESIDENCIAL_PARTS = ["no_res_estacionalizada", "no_estacionalizada"]

# Todas las series de la tabla (para el check del schema y el orden de export).
SERIES = list(LABEL_TO_SERIE.values()) + [MAIN_SERIE]
