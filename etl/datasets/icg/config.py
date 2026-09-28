"""Config de la tabla etl_icg (formato long) para el núcleo genérico (etl.core.db).

Índice de Confianza en el Gobierno (UTDT), escala 0-5. Dos fuentes en la misma página:

  - la planilla (.xls): la serie principal `icg`, tal como la publica UTDT;
  - los microdatos de la encuesta (.dta, Stata): las APERTURAS, calculadas acá como media
    ponderada por mes. Ver `source_microdatos.py`.
"""

TABLE = "etl_icg"
KEY_COLS = ["serie", "date"]
VALUE_COLS = ["valor"]
ACTUAL_VIEW = "etl_icg_actual"

# Página "Descarga de datos" del ICG, de donde se resuelven los links al .xls y al .dta en
# cada corrida.
ID_ITEM_MENU = 28756

# Serie principal. Índice en escala 0-5, sale de la planilla.
MAIN_SERIE = "icg"

# --- Aperturas (microdatos) -----------------------------------------------------------------
#
# Todas en escala 0-5, media ponderada con `PESO` por ola (mes de la encuesta).

# Los 5 componentes: cada encuestado vale 5 si responde positivo y 0 si responde negativo o
# NS/NC (variables `*_rec` del .dta). El ICG de cada encuestado es el promedio de las cinco.
COMPONENTES = {
    "icg_eval_gob":    "eval_gob_rec",     # evaluación general del gobierno
    "icg_benef_gob":   "benef_gob_rec",    # gobierna para la mayoría (vs. para pocos sectores)
    "icg_adm_gp":      "adm_gp_rec",       # eficiencia en la administración del gasto público
    "icg_cor_gob":     "cor_gob_rec",      # honestidad (pocos o ningún funcionario corrupto)
    "icg_resol_prob":  "resol_prob_rec",   # capacidad para resolver los problemas del país
}

# ICG total por corte: {variable del .dta: {código: serie}}. Códigos según el libro de códigos
# de UTDT. `Region` NO se usa: viene vacía (~98% NaN) en las olas recientes.
CORTES = {
    "Zona": {1: "icg_zona_caba", 2: "icg_zona_bsas", 3: "icg_zona_interior"},
    "sexo": {0: "icg_sexo_femenino", 1: "icg_sexo_masculino"},
    "edad": {1: "icg_edad_18_29", 2: "icg_edad_30_49", 3: "icg_edad_50_mas"},
    "edu":  {1: "icg_edu_primario", 2: "icg_edu_secundario", 3: "icg_edu_terciario_univ"},
}

# Factor de ponderación que el libro de códigos manda aplicar a toda estadística.
PESO = "ponderacion_UTDT"

# Casos (sin ponderar) mínimos para publicar una celda (serie, mes). Con 600-1200 casos por
# ola la celda más chica medida en toda la historia es edu=Primario con 44 casos (edad 18-29:
# 46), así que hoy no se descarta nada: es un piso de seguridad por si cambia el diseño muestral.
# Ojo igual con el ruido: en el último año esas dos celdas rondan 75-95 casos por mes.
MIN_CASOS = 30

# Control de integridad: el ICG total recalculado desde los microdatos contra la planilla.
# 0.005 y no 0.001 porque varios meses de la planilla están redondeados a 2 decimales
# (2026-05 = 1.99 vs 1.991 de los microdatos). Ver `run.py`.
TOLERANCIA_CONTROL = 0.005

APERTURAS = list(COMPONENTES) + [s for codigos in CORTES.values() for s in codigos.values()]
SERIES = [MAIN_SERIE] + APERTURAS
