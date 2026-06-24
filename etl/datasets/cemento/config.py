"""Config de la tabla de despacho de cemento (formato LONG) para el núcleo (etl.core.db).

Pasó de wide (valor + 3 columnas extra) a long: 1 fila por (serie, date). Series:
`despacho_nacional` (la principal) + `exportacion`/`consumo_despacho_nacional`/
`importaciones_propias` (estas 3 solo aparecen en filas 'definitivo'). La desest X-13 corre
solo sobre la serie principal (`despacho_nacional`).
"""

TABLE = "cemento_despacho"
KEY_COLS = ["serie", "date"]
VALUE_COLS = ["valor"]
ACTUAL_VIEW = "cemento_despacho_actual"

# Series (orden estable). Coinciden con el CHECK de schema.sql y con las claves que devuelve
# source.parse_*  (el parser ya las nombra así, salvo que antes 'despacho_nacional' iba a 'valor').
SERIES = ["despacho_nacional", "exportacion", "consumo_despacho_nacional",
          "importaciones_propias"]
MAIN_SERIE = "despacho_nacional"
