"""Config de la tabla etl_fob_granos (formato long) para el núcleo genérico (etl.core.db).

Dataset DIARIO: `date` es la fecha de cotización real, no el primer día del mes. Los promedios
mensuales salen de la vista `etl_fob_granos_mensual`, no de la tabla.

La clave incluye la VENTANA DE EMBARQUE además de (producto, date). La API publica, para cada
día y cada posición, una curva forward: varias filas con distinto `mesDesde/mesHasta`. Se
guardan TODAS —no sólo la que usa el cálculo de PRP— porque la fuente sólo se consulta un día
por request: rehacer el histórico cuesta ~8.500 requests contra un host que ya bloqueó la IP por
volumen (ver load_history). Descartar filas en la ingesta convertiría cualquier cambio de
criterio futuro en un re-scrapeo completo. Quedarse con una fila por día es trabajo de la vista.
"""

TABLE = "etl_fob_granos"
KEY_COLS = ["producto", "date", "embarque_desde", "embarque_hasta"]
VALUE_COLS = ["valor"]
ACTUAL_VIEW = "etl_fob_granos_actual"
# Días que la fuente declaró sin cotización: evita re-pedirlos en cada backfill.
SIN_DATO_TABLE = "etl_fob_granos_sin_dato"
MENSUAL_VIEW = "etl_fob_granos_mensual"

ESTADO = "oficial"

# Posiciones arancelarias (NCM + sufijo SIM + dígito verificador) de los cuatro granos del TCR.
# Son las variantes "A granel con hasta un 15 % embolsado": la misma NCM publica también la
# versión embolsada, ~20 USD/ton más cara, y elegir la equivocada corre toda la serie.
# Verificadas contra la planilla TCR Granos: mayo-2026 da 434,50 / 233,11 / 210,83 / 479,47
# USD/ton contra 434 / 233 / 210 / 479 de la planilla (que trunca a entero).
PRODUCTOS = {
    "soja":    ("12019000190C", "1201-90-00", "Habas de soja, Los Demás, a granel con hasta 15% embolsado"),
    "trigo":   ("10019900110W", "1001-99-00", "Trigo, Trigo Pan, a granel con hasta 15% embolsado"),
    "maiz":    ("10059010190Y", "1005-90-10", "Maíz, Los demás. En grano., a granel con hasta 15% embolsado"),
    "girasol": ("12060090910Y", "1206-00-90", "Semilla de Girasol, únicamente para industria, Los Demás, a granel con hasta 15% embolsado"),
}
POSICIONES = {pos: prod for prod, (pos, _, _) in PRODUCTOS.items()}

# Primer día con datos en la API (probado: 04/01/1993 responde; la planilla arranca en 1993-01).
START = "1993-01-04"

# Pesos de la canasta y derechos de exportación: tablas de referencia que este dataset consume
# pero NO escribe. Las mantiene un CRUD aparte; el seed inicial sale de la planilla.
DEX_TABLE = "dex"
VBP_TABLE = "vbp_granos"
