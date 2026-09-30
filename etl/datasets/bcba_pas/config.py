"""Config de etl_bcba_pas (Bolsa de Cereales de Buenos Aires, Panorama Agrícola Semanal).

Fuente: tabla `Histórico_PAS` del tablero público de Power BI de la BCBA ("Publicar en la web").
La BCBA publica el PAS los jueves; el tablero guarda UNA sola foto por (cultivo, campaña, zona) y la
pisa cada semana. Nuestra tabla append-only es lo que conserva las vintages semanales.

Formato LONG: 1 fila por (cultivo, campania, zona_id, variable) -> valor. Se eligió long (y no
wide como estimaciones_agricolas) porque:
  - la vista unificada `estimaciones_actual` es long: el mapeo de variables/unidades queda en un
    lugar y sin UNPIVOT;
  - en plena campaña las columnas se llenan de a una (avance de siembra antes que rinde): en long
    una celda vacía es "no hay fila" y una revisión sólo escribe la variable que cambió.

`fecha_datos` ("Datos al: 23/09/26", medida medidas_Proyecto[Datos_al]) y `last_refresh`
(LastRefreshTime del modelo) son CONTEXTO, no clave, con el mismo criterio que `date_informe` en
estimaciones_semanal/mensual: un valor que se repite entre semanas no genera fila; uno revisado sí,
con la fecha de datos en la que apareció. `_actual` = el de la fecha de datos más reciente.
"""

TABLE = "etl_bcba_pas"
KEY_COLS = ["cultivo", "campania", "zona_id", "variable"]
VALUE_COLS = ["valor"]
# Se escriben con cada snapshot pero no se comparan para el dedup.
EXTRA_COLS = ["zona", "fecha_datos", "last_refresh"]
ACTUAL_VIEW = "etl_bcba_pas_actual"
RELEASES_TABLE = "etl_bcba_pas_releases"
ESTADO = "publicado"

# ultimo_dato = "Datos al" del último release procesado (frescura de la FUENTE).
ULTIMO_DATO_SQL = f"select max(fecha_datos) from {RELEASES_TABLE}"

# Reporte publicado: parámetro r= de la URL y su clave (la 'k' del JSON base64 de r=).
R_PARAM = ("eyJrIjoiNWE2ZWJiODgtZWYzMy00YWRlLWJiYmEtOWUyM2FhOTVjZWMzIiwidCI6Ijg5MWFjN2RjLWRjMjU"
           "tNDQwMC1iMDY3LTlhNTQyM2YyOWE3MiJ9")
KEY = "5a6ebb88-ef33-4ade-bbba-9e23aa95cec3"
URL = f"https://app.powerbi.com/view?r={R_PARAM}"

FACT = "Histórico_PAS"
# Columna de la fuente -> variable. Los nombres del modelo están hechos a mano ("Perdído", espacios
# inconsistentes): si la BCBA los toca, la consulta falla y la corrida aborta (ver run.py).
#   sup_sembrada_ha          "Sembrado (Ha)": estimación de área sembrada de la campaña
#   avance_siembra_pct       "Avance siembra (%)"
#   sup_sembrada_al_dia_ha   "Sembradas (Ha)": hectáreas ya sembradas a la fecha
#   sup_perdida_ha           "Perdído(Ha)": área perdida (no se cosecha)
#   sup_cosechable_ha        "Cosechado(Ha)": sembrada - perdida (área a cosechar)
#   avance_cosecha_pct       "Avance cosecha (%)"
#   sup_cosechada_al_dia_ha  "Cosechadas (Ha)": hectáreas ya cosechadas a la fecha
#   rinde_qq_ha              "Rinde(qq/Ha)": quintales por hectárea, TAL COMO SE PUBLICA
#   produccion_t             "Producción(MTn)": el rótulo dice MTn (millones de toneladas, como
#                            muestra el tablero) pero el VALOR viene en toneladas: soja 2025/26
#                            nacional = 50.100.000. Verificado: rinde x cosechable / 10 cierra.
VARIABLES = {
    "Sembrado (Ha)": "sup_sembrada_ha",
    "Avance siembra (%)": "avance_siembra_pct",
    "Sembradas (Ha)": "sup_sembrada_al_dia_ha",
    "Perdído(Ha)": "sup_perdida_ha",
    "Cosechado(Ha)": "sup_cosechable_ha",
    "Avance cosecha (%)": "avance_cosecha_pct",
    "Cosechadas (Ha)": "sup_cosechada_al_dia_ha",
    "Rinde(qq/Ha)": "rinde_qq_ha",
    "Producción(MTn)": "produccion_t",
}

# Dimensiones del modelo: se consultan en cada corrida y se VERIFICAN contra esto (por id). Un id
# nuevo o un nombre distinto aborta la corrida: cultivo o zona nueva = decisión explícita.
CULTIVOS = {1: ("Soja", "soja"), 2: ("Maíz", "maiz"), 3: ("Trigo", "trigo"),
            4: ("Girasol", "girasol"), 5: ("Cebada", "cebada"), 6: ("Sorgo", "sorgo")}
# id -> (código en `Zona`, `Descripción`). Zonas PAS de la BCBA (I a XV).
#   80 = Nacional: SUMA de las 15 zonas (verificado: cierra al redondeo con la suma).
#   0  = "Actual": la estimación NACIONAL oficial que comunica la BCBA (redondeada, p.ej. maíz
#        2025/26 = 64,0 Mt) mientras la 80 es la suma de zonas (63.573.143 t con la cosecha al 99%).
#        Existe sólo desde 2022/23. NO son lo mismo: no fusionar ni sumar la 0 con la 80.
ZONAS = {
    0: (None, "Actual"), 1: ("I", "NOA"), 2: ("II", "NEA"), 3: ("III", "CN de Córdoba"),
    4: ("IV", "S de Córdoba"), 5: ("V", "CN de SF"), 6: ("VI", "Núcleo Norte"),
    7: ("VII", "Núcleo Sur"), 8: ("VIII", "CE de ER"), 9: ("IX", "N LP - O BA"),
    10: ("X", "Centro de BA"), 11: ("XI", "SO BA - S LP"), 12: ("XII", "Sudeste de BA"),
    13: ("XIII", "San Luis"), 14: ("XIV", "Cuenca Salado"), 15: ("XV", "Corr.-Mis."),
    80: ("Nacional", "Nacional"),
}
# Código que se guarda en `zona` (texto legible; la clave es zona_id).
ZONA_CODIGO = {i: (c if c else "actual") for i, (c, _) in ZONAS.items()}
ZONA_CODIGO[80] = "nacional"

# Campañas: `id_campaña` = año inicial - 2009 (0 = 2009/2010). Se valida la regla, no una lista:
# la campaña nueva de cada año entra sola si respeta la regla.
CAMPANIA_ID_BASE = 2009

# Plausibilidad: al 23/09/2026 la tabla trae 1.683 filas (crece ~100 por campaña).
MIN_FILAS = 1500
MAX_FILAS = 6 * 40 * len(ZONAS)
