"""Config de la tabla etl_cot (Commitments of Traders de la CFTC) para el núcleo genérico.

Dataset SEMANAL: `date` es la fecha de corte del reporte (martes), no el primer día del mes.
La CFTC publica los viernes a las 15:30 ET con el corte del martes anterior.

Formato ANCHO, no long: la clave es (contrato, categoria, tipo, date) y las cuatro posiciones
—largo, corto, spreading e interés abierto— son columnas de valor de la misma fila. Es la
excepción al formato long del resto del repo, y es a propósito: las cuatro salen SIEMPRE juntas
de la misma fila del archivo de la CFTC, nunca falta una sola, y la pregunta natural
—"posición neta a lo largo del tiempo"— en formato long obliga a un self-join. Además
`insert_if_changed` deduplica mirando las cuatro juntas, que es exactamente lo que hace falta:
cuando la CFTC revisa una semana, revisa la fila entera.

DOS REPORTES DISTINTOS, y no se empalman:

  `managed_money`   Reporte Disaggregated. Desde 2006-06-13. Es la categoría que sigue el
                    mercado: fondos de gestión activa (CTAs, hedge funds de commodities).
  `non_commercial`  Reporte Legacy. Desde 1986-01-15. Categoría MÁS AMPLIA: incluye a los
                    managed money y además a otros especuladores reportables. Es el único
                    dato especulativo que existe antes de 2006, pero NO es la misma serie.

Conviven como dos series separadas, completas, sin pegar. Empalmarlas es una decisión de
análisis y se toma al consumir, no acá.
"""

TABLE = "etl_cot"
KEY_COLS = ["contrato", "categoria", "tipo", "date"]
VALUE_COLS = ["largo", "corto", "spreading", "interes_abierto"]
ACTUAL_VIEW = "etl_cot_actual"

ESTADO = "publicado"

# Los códigos de contrato CAMBIARON en 1998, sin ningún solape: el último dato del código viejo
# es el 1997-12-30 y el primero del nuevo el 1998-01-06. Por eso cada grano lleva los dos y la
# ingesta los unifica bajo el mismo `contrato`. Sin esto la serie arranca en 1998 y nadie se
# entera de que faltan doce años.
CONTRATOS = {
    "soja":      ("005601", "005602", "SOYBEANS - CHICAGO BOARD OF TRADE"),
    "maiz":      ("002601", "002602", "CORN - CHICAGO BOARD OF TRADE"),
    "trigo_srw": ("001601", "001602", "WHEAT-SRW - CHICAGO BOARD OF TRADE"),
}
CODIGOS = {c: g for g, (viejo, nuevo, _) in CONTRATOS.items() for c in (viejo, nuevo)}

# Hasta 1997 la CFTC reportaba los granos en MILES DE BUSHELS y desde el 1998-01-06 en
# CONTRATOS. Los tres contratos son de 5.000 bushels, así que el factor es 5.
#
# No es una corazonada: al cruzar el 1998-01-06 el interés abierto de los CUATRO contratos de
# granos del archivo cae en un factor de 4,65 a 5,51 (trigo CBOT 4,65 / trigo Kansas 5,51 /
# maíz 4,70 / soja 5,12), mientras que contratos que NO se miden en bushels no se mueven:
# boneless beef trimmings da 1,05 y electricidad CA-OR 0,75. Si fuera un cambio general de
# unidad de reporte, esos dos también saltarían. La dispersión alrededor de 5 es movimiento real
# de mercado: la comparación es entre el 2º semestre de 1997 y el 1º de 1998, no entre dos
# semanas contiguas — no hay ninguna fecha en común entre los dos códigos.
#
# La tabla guarda el valor CRUDO y la unidad; la conversión vive en la vista etl_cot_neto, para
# que la inferencia quede a la vista y se pueda revertir tocando un solo lugar.
UNIDAD_CAMBIA_DESDE = "1998-01-06"
FACTOR_MILES_BUSHELS = 5.0
UNIDAD_VIEJA = "miles_bushels"
UNIDAD_NUEVA = "contratos"

# Las cuatro combinaciones (reporte x tipo) que se ingestan. Para cada una: el dataset de la API
# Socrata para el incremental, y los zips de la carga histórica.
#
# `hist` es el archivo multianual que cubre todo hasta 2016, y `anual` el patrón de los archivos
# año por año de 2017 en adelante. El patrón es un format string y no un prefijo porque la CFTC
# nombra los archivos de forma inconsistente: `deacot2026` sin separador y `fut_disagg_txt_2026`
# con guión bajo.
#
# El `tipo` sale del open interest, que es lo que los distingue: soja al 2026-09-08 da 1.070.401
# contratos en `futures` y 1.378.220 en `futures_options`.
ORIGENES = {
    ("managed_money", "futures"):          {"socrata": "72hh-3qpy",
                                            "hist": "fut_disagg_txt_hist_2006_2016",
                                            "anual": "fut_disagg_txt_{anio}",
                                            "layout": "disagg"},
    ("managed_money", "futures_options"):  {"socrata": "kh3c-gbw2",
                                            "hist": "com_disagg_txt_hist_2006_2016",
                                            "anual": "com_disagg_txt_{anio}",
                                            "layout": "disagg"},
    ("non_commercial", "futures"):         {"socrata": "6dca-aqww",
                                            "hist": "deacot1986_2016",
                                            "anual": "deacot{anio}",
                                            "layout": "legacy"},
    ("non_commercial", "futures_options"): {"socrata": "jun7-fc8e",
                                            "hist": "deahistfo_1995_2016",
                                            "anual": "deahistfo{anio}",
                                            "layout": "legacy"},
}

# Primer año que NO cubre el archivo multianual: de acá en adelante se bajan los anuales.
PRIMER_ANIO_ANUAL = 2017

# La API Socrata NO tiene todo el histórico: legacy arranca en 1998-01-06 y disaggregated en
# 2006-06-13. El tramo 1986-1997 de legacy existe SÓLO en los zips. Por eso `run` (incremental)
# va por API y `load-history` por zips: no son dos caminos al mismo dato.
API_DESDE = {"managed_money": "2006-06-13", "non_commercial": "1998-01-06"}
SERIE_DESDE = {"managed_money": "2006-06-13", "non_commercial": "1986-01-15"}
