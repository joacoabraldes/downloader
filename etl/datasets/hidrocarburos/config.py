"""Config de la tabla etl_hidrocarburos (formato long) para el núcleo genérico (etl.core.db).

Igual que automotriz, la clave incluye `serie`: conviven varias series independientes en una
sola tabla. Son dos datasets del mismo organismo (Secretaría de Energía), con la misma API,
la misma cadencia y el mismo parser, así que van juntos: separarlos duplicaría source/run/
schema para no ganar nada.

Dos niveles de serie:

  - TOTALES: la producción del mes. Es el número que se publica y se sigue, tiene histórico
    desde 1996 y es lo único que se desestacionaliza.
  - TIPOS: el desagregado por tipo de recurso, que la fuente trae en el MISMO CSV (no cuesta
    un request extra). Arranca en 2009 porque ahí arranca el dato por pozo. Se guarda crudo:
    es el que muestra el corrimiento de convencional a shale.

Unidades: `petroleo` en miles de m3, `gas` en millones de m3 (mm3). Ver `source.py`.
"""

TABLE = "etl_hidrocarburos"
KEY_COLS = ["serie", "date"]
VALUE_COLS = ["valor"]
ACTUAL_VIEW = "etl_hidrocarburos_actual"

# Series principales (total del mes). Son las que tienen histórico y las que se desestacionalizan.
TOTALES = ["petroleo", "gas"]

# Tipo de recurso, tal como lo desagrega la fuente en su columna `concepto`.
TIPOS = ["convencional", "shale", "tight"]

# Orden estable. Coincide con el CHECK de schema.sql.
SERIES = TOTALES + [f"{t}_{tipo}" for t in TOTALES for tipo in TIPOS]
