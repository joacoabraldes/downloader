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

# Deflactores: de dónde sale el índice de precios que pasa de valores corrientes a constantes, y
# mes base en el que quedan expresados los valores reales.
#
# Son DOS, uno por moneda, y los dos salen de `public.deflactores`, que ya publica ambos con la
# misma forma (fecha, indice, origen):
#
#   'ipc_largo'      pesos.   NO es el `ipc_nacional` de este dataset: ese arranca en 2016-12 y
#                    dejaba a `ripte` (nominal desde 1994) y `smvm` (desde 1965) sin valor real
#                    en casi toda su historia. `ipc_largo` cubre desde 1990-01.
#   'uscpi_mensual'  dólares. CPI-U del BLS (serie CUUR0000SA0, all items, NOT seasonally
#                    adjusted), desde 1913-01. Es el deflactor de `expo_total` e `impo_total`.
#
# Que el CPI sea NSA no es un detalle: si se deflactara con la versión desestacionalizada del
# CPI, la serie real quedaría con la estacionalidad del deflactor invertida encima, y el X-13
# posterior estaría ajustando un artefacto nuestro además de la estacionalidad del comercio.
#
# Los dos los mantiene otro repo (/home/jmt/dev/downloaders_viejos/downloader). Los detalles
# —dependencia externa, valores de la columna `origen`, precisión del tramo pre-2016— están en
# el comentario de la vista.
#
# El mes base es MÓVIL y POR SERIE: cada serie queda expresada en moneda de SU último dato
# observado, y toda su historia se reexpresa hacia atrás en esa moneda. Las 11 deflactables
# terminan en meses distintos, así que no comparten base: la fecha efectiva de cada fila viaja
# en la columna `mes_base` de la vista. No se escribe una fecha acá porque se calcula sola.
#
# El deflactor entra completo, observado y proyectado por igual: las proyecciones de
# `inflacion_proyectada` son las que permiten deflactar los meses que el índice todavía no
# cubre. Para las series en dólares esto casi nunca se usa: el BLS publica el CPI de un mes a
# mitad del siguiente, bastante antes que el INDEC el comercio exterior de ese mismo mes.
#
# Estas dos constantes son DOCUMENTACIÓN: ningún código las lee, la lógica vive en la vista
# etl_datos_gob_real y el deflactor de cada serie sale de SERIES_META. Cambiar la base es editar
# la vista, y actualizar acá.
SERIE_DEFLACTOR = "public.deflactores (deflactor='ipc_largo' | 'uscpi_mensual', según la serie)"
MES_BASE_REAL = "último mes publicado del deflactor de cada serie (base móvil; ver mes_base)"

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

# serie -> (id en la API, nombre legible, unidad, organismo, deflactor)
#
# `deflactor` marca las series expresadas en VALORES CORRIENTES, es decir las que mezclan
# variación real con inflación, y dice con qué índice se las deflacta:
#
#   None             la serie no se deflacta.
#   'ipc_largo'      serie en pesos corrientes.
#   'uscpi_mensual'  serie en dólares corrientes.
#
# Estar en dólares NO exime de deflactar, y por un tiempo acá se creyó que sí. Un dólar de 1992
# compra bastante más que uno de 2026: comparar el nivel de exportaciones de los 90 contra el de
# hoy en dólares nominales sobrestima el crecimiento por toda la inflación de EEUU del medio.
# Por eso expo/impo van con 'uscpi_mensual', no con None.
#
# Quedan sin deflactor los índices de volumen (isac, ipi), que ya son cantidades, y el propio
# ipc_nacional. Los índices de salarios SÍ llevan: son índices nominales, así que deflactados
# dan el salario real, que es su uso natural.
#
# El orden es el de presentación en la dimensión.
SERIES_META = {
    "isac": (
        "33.2_ISAC_NIVELRAL_0_M_18_63",
        "ISAC. Actividad de la construcción, nivel general",
        "índice 2004=100", "INDEC", None),
    "ipi_manufacturero": (
        "453.1_SERIE_ORIGNAL_0_0_14_46",
        "IPI manufacturero, nivel general (serie original)",
        "índice 2004=100", "INDEC", None),
    "ipc_nacional": (
        "148.3_INIVELNAL_DICI_M_26",
        "IPC. Nivel general nacional",
        "índice dic-2016=100", "INDEC", None),
    "expo_total": (
        "74.3_IET_0_M_16",
        "Exportaciones totales",
        "USD millones", "INDEC", "uscpi_mensual"),
    "impo_total": (
        "74.3_IIT_0_M_25",
        "Importaciones totales",
        "USD millones", "INDEC", "uscpi_mensual"),
    "ventas_supermercados": (
        "455.1_VENTAS_TOTLOS_0_M_30_43",
        "Ventas en supermercados, total por grupo de artículos",
        "miles de pesos", "INDEC", "ipc_largo"),
    "ventas_centros_compras": (
        "458.1_TOTALTAL_ABRI_M_5_38",
        "Ventas en centros de compras, total",
        "pesos", "INDEC", "ipc_largo"),
    "ripte": (
        "158.1_REPTE_0_0_5",
        "RIPTE. Remuneración imponible promedio de los trabajadores estables",
        "pesos corrientes", "Secretaría de Trabajo", "ipc_largo"),
    "smvm": (
        "57.1_SMVMM_0_M_34",
        "Salario mínimo, vital y móvil (mensual)",
        "pesos corrientes", "Secretaría de Trabajo", "ipc_largo"),
    # Índice de salarios (INDEC), base oct-2016=100. Las 5 aperturas que publica el organismo.
    # OJO: `SOR_PRIADO` aparece en dos ids que sólo difieren en el sufijo — 0_25 es el privado
    # REGISTRADO y 0_28 el privado NO registrado. Es facilísimo cruzarlos.
    "indice_salarios_total": (
        "149.1_TL_INDIIOS_OCTU_0_21",
        "Índice de salarios. Nivel general",
        "índice oct-2016=100", "INDEC", "ipc_largo"),
    "indice_salarios_registrado": (
        "149.1_TL_REGIADO_OCTU_0_16",
        "Índice de salarios. Empleo registrado (total)",
        "índice oct-2016=100", "INDEC", "ipc_largo"),
    "indice_salarios_priv_registrado": (
        "149.1_SOR_PRIADO_OCTU_0_25",
        "Índice de salarios. Empleo registrado, sector privado",
        "índice oct-2016=100", "INDEC", "ipc_largo"),
    "indice_salarios_publico": (
        "149.1_SOR_PUBICO_OCTU_0_14",
        "Índice de salarios. Empleo registrado, sector público",
        "índice oct-2016=100", "INDEC", "ipc_largo"),
    "indice_salarios_priv_no_registrado": (
        "149.1_SOR_PRIADO_OCTU_0_28",
        "Índice de salarios. Empleo no registrado, sector privado",
        "índice oct-2016=100", "INDEC", "ipc_largo"),
}

# serie -> id en la API de su versión DESESTACIONALIZADA, cuando el organismo la publica.
#
# Para varias series INDEC publica la ajustada como una serie APARTE, con su propio id. Para esas
# lo correcto es bajar la oficial y NO correrles X-13 nosotros encima (ver etl/series_desest.toml).
#
# POR QUÉ NO ES UNA FILA MÁS EN SERIES_META: si entrara como serie propia ('isac_desest'), el
# consumidor tendría que saber de antemano que ese slug existe, y `valor_desest` de 'isac' seguiría
# en NULL — el dato estaría en la base pero no donde se lo busca. Entra entonces por el MISMO
# carril que el X-13 propio: estado='desestacionalizado' bajo el slug de la serie base, que es lo
# que lee `etl_datos_gob_desest` y termina en la columna `valor_desest`.
#
# CÓMO SE DISTINGUE UNA DE OTRA, que es el punto: la fila lo dice.
#   fuente      URL de la serie en la API   vs.  'census x13'
#   parametros  {"origen": "indec", ...}    vs.  los parámetros de la corrida X-13
# Y `etl_datos_gob_completo` expone las dos como `desest_fuente` / `desest_parametros`, así que la
# procedencia viaja con el dato sin tener que ir a buscarla a otra tabla.
#
# INVARIANTE: una serie está acá o en el bloque [datos_gob] de series_desest.toml, nunca en los
# dos. Las dos rutas escriben la misma fila (serie, date, estado='desestacionalizado') y el unique
# index parcial deja una sola: la última en correr pisaría a la otra en silencio. `run.py` valida.
DESEST_OFICIAL = {
    "isac": "33.2_ISAC_SIN_EDAD_0_M_23_56",              # "ISAC. Nivel General. Sin Estacionalidad"
    "ipi_manufacturero": "453.1_SERIE_DESEADA_0_0_24_58",  # "IPI Nivel General Serie Desestacionalizada"
}

# Cuadros CSV del INDEC. Para las series que aparecen acá, el CSV es la fuente PRIMARIA y la API
# de series de tiempo pasa a ser el RESPALDO. Es la única excepción a "todo sale de la API".
#
# POR QUÉ SE INVIRTIÓ LA PRIORIDAD, con el número que lo motivó: el feed de apis.datos.gob.ar va
# atrasado respecto de lo que el organismo ya publicó. Medido el 2026-09-10 sobre las 5 series del
# índice de salarios:
#
#     meses solapados API vs CSV : 611
#     discrepancias (>0.01)      :   0
#     meses que sólo tiene el CSV:  10   (2026-05 y 2026-06 de las 5 series)
#
# 611 meses idénticos dígito por dígito, y el CSV dos meses adelante. No son dos estimaciones de
# lo mismo: es el mismo dato por un canal que publica antes. Con eso, dejar la API como primaria
# significaba mostrar abril cuando junio ya estaba publicado.
#
# El CSV además ARRANCA en la misma fecha que la API serie por serie y la contiene por completo
# (superset estricto), así que invertir la prioridad no recorta histórico.
#
# NO se usa el PDF del informe de prensa, que fue la primera idea: sus tres cuadros publican sólo
# variaciones (mensual, interanual, acumulada) redondeadas a UN decimal. Reconstruir el nivel
# encadenando esas variaciones acumula error de redondeo y nunca vuelve a cuadrar con la serie
# oficial. El CSV publica el NIVEL, que es lo que guardamos.
#
# La URL es fija, sin fecha en el nombre: no hay que scrapear un link para llegar al mes nuevo.
SERIES_CSV = {
    "indice_salarios": {
        "url": "https://www.indec.gob.ar/ftp/cuadros/sociedad/indice_salarios.csv",
        # columna en el CSV -> serie nuestra
        "columnas": {
            "IS_indice_total": "indice_salarios_total",
            "IS_total_registrado": "indice_salarios_registrado",
            "IS_sector_privado_registrado": "indice_salarios_priv_registrado",
            "IS_sector_publico": "indice_salarios_publico",
            "IS_sector_no_registrado": "indice_salarios_priv_no_registrado",
        },
    },
}

# serie -> cuadro que la publica. Derivado, para no repetir el mapeo al revés.
CSV_POR_SERIE = {serie: nombre
                 for nombre, cuadro in SERIES_CSV.items()
                 for serie in cuadro["columnas"].values()}

# Deflactores válidos. Ningún código de acá los interpreta: viajan tal cual a la columna
# `deflactor` de la dimensión, y la vista los usa para filtrar `public.deflactores`. Este set
# existe sólo para que un typo en SERIES_META falle en la corrida y no salga como una serie
# sin valor real, que es lo que pasaría con el JOIN vacío.
DEFLACTORES = {"ipc_largo", "uscpi_mensual"}

# Orden estable: fija el `orden` de la dimensión y las opciones de `--serie`.
#
# NO hay un CHECK en schema.sql contra el cual cuadrar esta lista, y es a propósito: `serie` no
# lleva CHECK ni FK justamente para que sumar una serie sea editar SERIES_META y nada más. Este
# comentario decía lo contrario y mandaba a buscar un constraint que no existe.
SERIES = list(SERIES_META)

# id de la API -> serie, para resolver la respuesta.
POR_ID = {meta[0]: serie for serie, meta in SERIES_META.items()}
