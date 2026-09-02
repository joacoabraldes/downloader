"""Config de la tabla etl_transferencias (formato long) para el núcleo genérico (etl.core.db).

Como automotriz o patentamientos, la clave incluye `serie`: la tabla queda preparada para
convivir con más de una, aunque hoy haya una sola.

Hoy se ingesta SOLO `autos`, que es lo que se pidió. El formulario de la DNRPA ofrece además
Motos (`M`) y Maquinarias (`Q`) con el MISMO cuadro y el MISMO parser, así que sumarlas es
agregar la entrada en `SERIES_TIPO` (más el CHECK de schema.sql y, si se quieren
desestacionalizar, su bloque en series_desest.toml). No se hizo de una porque cada serie nueva
pide su propia decisión de desestacionalización, y esas no se toman a ojo.
"""
from __future__ import annotations

import datetime as dt

TABLE = "etl_transferencias"
KEY_COLS = ["serie", "date"]
VALUE_COLS = ["valor"]
ACTUAL_VIEW = "etl_transferencias_actual"

# serie en la base -> `codigo_tipo` del formulario de la DNRPA (A=Autos, M=Motos, Q=Maquinarias).
SERIES_TIPO = {"autos": "A"}

# Series (orden estable). Coinciden con el CHECK de schema.sql.
SERIES = list(SERIES_TIPO)

# Serie principal (para la ventana incremental y como headline del dataset).
MAIN_SERIE = "autos"

# Primer año que ofrece el desplegable del formulario. El histórico arranca ahí.
INICIO = dt.date(1995, 1, 1)
