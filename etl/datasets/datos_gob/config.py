"""Config del dataset `datos_gob` (API oficial Series de Tiempo del Estado).

A diferencia del resto del repo —un scraper por fuente, porque cada fuente es un PDF o un Excel
distinto— acá hay UNA API y N series. Sumar una serie nueva es agregar una fila a SERIES_META,
no escribir código.

Las series NO comparten unidad (hay índices, dólares y pesos en la misma tabla), así que la
unidad y el nombre legible viven en la dimensión `etl_datos_gob_series`, igual que en
`reservas_pasivos`. El seed de esa dimensión se genera desde SERIES_META (ver schema.sql).

Los IDs se fijan acá y no se descubren en runtime: el buscador de la API matchea sólo título y
descripción —nunca IDs— y su relevancia es pobre (ver docs/datos_gob_ar.md).
"""

TABLE = "etl_datos_gob"
KEY_COLS = ["serie", "date"]
VALUE_COLS = ["valor"]
ACTUAL_VIEW = "etl_datos_gob_actual"

# Deflactor: de dónde sale el índice de precios que pasa de pesos corrientes a constantes, y
# mes base en el que quedan expresados los valores reales. El mes base es FIJO a propósito: con
# un base móvil ("pesos de hoy") toda la serie real se recalcularía cada mes y los valores
# dejarían de ser reproducibles, que es justo lo que este repo cuida (por algo guarda los
# parámetros de X-13 para poder auditar un número).
#
# NO es el `ipc_nacional` de este dataset: ese arranca en 2016-12 y dejaba a `ripte` (nominal
# desde 1994) y `smvm` (desde 1965) sin valor real en casi toda su historia. Se usa
# `public.deflactores` con deflactor='ipc_largo', que cubre desde 1990-01 y lo mantiene otro
# repo (/home/jmt/dev/downloaders_viejos/downloader). Los detalles —dependencia externa,
# columna `origen`, precisión del tramo pre-2016— están en el comentario de la vista.
#
# Estas dos constantes son DOCUMENTACIÓN: ningún código las lee, la lógica vive en la vista
# etl_datos_gob_real. Cambiar el deflactor o la base es editar la vista, y actualizar acá.
SERIE_DEFLACTOR = "public.deflactores (deflactor='ipc_largo')"
MES_BASE_REAL = "2026-06-01"   # los valores reales quedan en pesos de junio-2026

# Piso del valor REAL, por serie. Excepción, no regla: sólo va acá la serie cuya serie nominal
# NO es homogénea hacia atrás, y por eso deflactarla daría un número sin sentido.
#
# `smvm` publica el monto en la moneda de curso legal de cada época, y esa moneda cambió tres
# veces dentro de la serie: 1983-06 (peso ley -> peso argentino, /10.000), 1985-07 (peso
# argentino -> austral, /1.000) y 1992-01 (austral -> peso convertible, /10.000). En diciembre
# de 1991 el monto es 970.000 y en enero de 1992 es 97: no bajó el salario, cambió la unidad.
# El deflactor, en cambio, es un índice de poder adquisitivo y atraviesa esas reformas sin
# saltos. Deflactar australes contra él da un valor 10.000 veces más grande que el correcto.
#
# Se recorta y NO se convierte a propósito: reescalar los tramos viejos sería reescribir el
# valor que publica el organismo, y el `valor_nominal` de este repo tiene que seguir siendo lo
# que publica el organismo. Quien necesite 1965-1991 hace la conversión del lado del análisis.
REAL_DESDE = {
    "smvm": "1992-01-01",   # última reforma monetaria; antes el nominal está en otra moneda
}

# serie -> (id en la API, nombre legible, unidad, organismo, deflactable)
#
# `deflactable` marca las series expresadas en PESOS CORRIENTES, es decir las que mezclan
# variación real con inflación. Para esas, la vista etl_datos_gob_real publica además el valor
# a precios constantes. Se excluyen los índices de volumen (isac, ipi), el propio deflactor
# (ipc_nacional) y lo que ya está en dólares (expo/impo).
#
# Los índices de salarios SÍ van marcados: son índices nominales, así que deflactados dan el
# salario real, que es su uso natural.
#
# El orden es el de presentación en la dimensión.
SERIES_META = {
    "isac": (
        "33.2_ISAC_NIVELRAL_0_M_18_63",
        "ISAC. Actividad de la construcción, nivel general",
        "índice 2004=100", "INDEC", False),
    "ipi_manufacturero": (
        "453.1_SERIE_ORIGNAL_0_0_14_46",
        "IPI manufacturero, nivel general (serie original)",
        "índice 2004=100", "INDEC", False),
    "ipc_nacional": (
        "148.3_INIVELNAL_DICI_M_26",
        "IPC. Nivel general nacional",
        "índice dic-2016=100", "INDEC", False),
    "expo_total": (
        "74.3_IET_0_M_16",
        "Exportaciones totales",
        "USD millones", "INDEC", False),
    "impo_total": (
        "74.3_IIT_0_M_25",
        "Importaciones totales",
        "USD millones", "INDEC", False),
    "ventas_supermercados": (
        "455.1_VENTAS_TOTLOS_0_M_30_43",
        "Ventas en supermercados, total por grupo de artículos",
        "miles de pesos", "INDEC", True),
    "ventas_centros_compras": (
        "458.1_TOTALTAL_ABRI_M_5_38",
        "Ventas en centros de compras, total",
        "pesos", "INDEC", True),
    "ripte": (
        "158.1_REPTE_0_0_5",
        "RIPTE. Remuneración imponible promedio de los trabajadores estables",
        "pesos corrientes", "Secretaría de Trabajo", True),
    "smvm": (
        "57.1_SMVMM_0_M_34",
        "Salario mínimo, vital y móvil (mensual)",
        "pesos corrientes", "Secretaría de Trabajo", True),
    # Índice de salarios (INDEC), base oct-2016=100. Las 5 aperturas que publica el organismo.
    # OJO: `SOR_PRIADO` aparece en dos ids que sólo difieren en el sufijo — 0_25 es el privado
    # REGISTRADO y 0_28 el privado NO registrado. Es facilísimo cruzarlos.
    "indice_salarios_total": (
        "149.1_TL_INDIIOS_OCTU_0_21",
        "Índice de salarios. Nivel general",
        "índice oct-2016=100", "INDEC", True),
    "indice_salarios_registrado": (
        "149.1_TL_REGIADO_OCTU_0_16",
        "Índice de salarios. Empleo registrado (total)",
        "índice oct-2016=100", "INDEC", True),
    "indice_salarios_priv_registrado": (
        "149.1_SOR_PRIADO_OCTU_0_25",
        "Índice de salarios. Empleo registrado, sector privado",
        "índice oct-2016=100", "INDEC", True),
    "indice_salarios_publico": (
        "149.1_SOR_PUBICO_OCTU_0_14",
        "Índice de salarios. Empleo registrado, sector público",
        "índice oct-2016=100", "INDEC", True),
    "indice_salarios_priv_no_registrado": (
        "149.1_SOR_PRIADO_OCTU_0_28",
        "Índice de salarios. Empleo no registrado, sector privado",
        "índice oct-2016=100", "INDEC", True),
}

# Orden estable. Coincide con el CHECK de schema.sql.
SERIES = list(SERIES_META)

# id de la API -> serie, para resolver la respuesta.
POR_ID = {meta[0]: serie for serie, meta in SERIES_META.items()}
