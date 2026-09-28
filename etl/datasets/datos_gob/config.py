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
#                    adjusted), desde 1913-01. Es el deflactor de `expo_total`, `impo_total` y
#                    `saldo_total`.
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
# observado, y toda su historia se reexpresa hacia atrás en esa moneda. Las 12 deflactables
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
# Por eso expo/impo/saldo van con 'uscpi_mensual', no con None.
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
    # Saldo comercial: la columna 'Saldo' de la planilla del INDEC (primaria, ver SERIES_XLS), NO
    # expo - impo calculado acá. El id de la API es el respaldo: misma familia 74.3 que expo/impo
    # y, contra la API, igual a 74.3_IET - 74.3_IIT hasta el error de punto flotante (4.5e-13,
    # medido el 2026-09-18). Deflactado con el mismo CPI que expo/impo: es la diferencia de dos
    # flujos en dólares corrientes, y deflactar ambos por el mismo índice y restar da lo mismo
    # que restar y deflactar. Puede ser NEGATIVO: por eso no pasa por X-13 (ver el toml).
    "saldo_total": (
        "74.3_ISC_0_M_19",
        "Saldo comercial",
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

# serie -> edad máxima legítima, en días, de su último dato (`current_date - max(date)`). Lo lee
# la vista `etl_datos_gob_salud`, una fila por serie, y de ahí `etl_control_salud` marca el
# dataset entero como DATO_VIEJO si CUALQUIER serie se pasa.
#
# POR QUÉ POR SERIE Y NO UN UMBRAL DEL DATASET: son 15 series de organismos distintos, cada una
# con su calendario. Un umbral sobre el max(date) del dataset es ciego por construcción: `smvm`
# trae meses FUTUROS (el salario mínimo se fija por decreto con meses de anticipación, hoy hasta
# 2027-04), así que ese max nunca envejece. Y aun sin eso, basta que publique la serie más
# rápida para que una congelada pase inadvertida: los 5 `indice_salarios_*` llegaron a estar
# 162 días parados con el control en `ok`.
#
# La cuenta es la misma que en `etl/schema_control_salud.sql`:
#
#     dias_max_dato = EDAD DEL LABEL AL APARECER EN LA BASE + un periodo (31) + margen
#
# La edad del label se MIDIÓ el 2026-09-18 como `min(ingested_at)::date - date` de cada mes
# nuevo, descartando los lotes del backfill inicial del 2026-08-12 (cientos de meses con el mismo
# ingested_at). Es poca historia: un mes de corridas incrementales, una o dos observaciones por
# serie. Por eso los márgenes son anchos y cada valor dice de dónde sale. Reajustar cuando haya
# varios meses de incremental real.
#
# OJO: la edad medida es la de la fuente PRIMARIA de cada serie (API, CSV o XLS), no la del
# organismo. apis.datos.gob.ar puede ir semanas atrás del INDEC (expo/impo de julio: INDEC lo
# publicó a mediados de agosto y la API lo trajo el 16-sep), así que una serie servida por la
# API tiene que tolerar ese atraso o daría falsa alarma. Las que tienen un cuadro del INDEC como
# primaria (SERIES_CSV, SERIES_XLS) se miden contra el cuadro, y su umbral es más corto.
#
# Sumar una serie a SERIES_META exige sumarla acá: `run.py` valida que estén las mismas.
DIAS_MAX_DATO = {
    "isac":                   115,  # jul visto 09-sep -> 70 d. 70 + 31 + margen
    "ipi_manufacturero":      115,  # jul visto 09-sep -> 70 d. Mismo informe que isac
    "ipc_nacional":            90,  # jul visto 14-ago (44 d), ago visto 12-sep (42 d). 44 + 31 + margen
    # Comercio exterior: planilla del INDEC primaria (ver SERIES_XLS). El ICA sale a mitad del mes
    # siguiente, 16:00; ago visto 18-sep (48 d) en la corrida de las 17:30, el mismo día. Justo
    # antes del ICA siguiente el último dato tiene ~80 d (ago contra el ~20-oct). 48 + 31 +
    # margen para un ICA que se corra una semana. Con la API sola (jul visto 16-sep, 77 d) el
    # umbral era 120; con 100, una planilla caída varias semanas salta también por frescura, que
    # es lo buscado: la API de respaldo va ~4 semanas atrás.
    "expo_total":             100,
    "impo_total":             100,  # idem expo_total, misma planilla
    "saldo_total":            100,  # idem expo_total, misma planilla
    "ventas_supermercados":   140,  # jun visto 31-ago -> 91 d. 91 + 31 + margen
    "ventas_centros_compras": 140,  # idem supermercados, mismo informe
    "ripte":                  120,  # jun visto 15-ago (75 d), jul visto 13-sep (74 d). 75 + 31 + margen
    # `smvm` trae meses futuros y el umbral compara contra el ÚLTIMO mes fijado, así que la edad
    # es negativa hasta que ese mes llega; la alarma salta si pasado el cronograma no aparece el
    # decreto siguiente. Observado: sep-2026 entró el 13-sep (12 d) junto con oct..abr-2027, y
    # antes ago-2026 llegó a tener ~43 d sin reemplazo. 43 + 31 + margen: la fijación se demora
    # cuando el Consejo del Salario no acuerda y el gobierno termina laudando por decreto.
    "smvm":                    90,
    # Índice de salarios: CSV del INDEC primario (ver SERIES_CSV). jun visto 10-sep -> 101 d, y es
    # COTA SUPERIOR: el CSV se sumó como fuente ese mismo día, así que jun pudo estar antes. El
    # may visto el mismo día (132 d) no cuenta: es la puesta al día del cambio de fuente, no un
    # rezago. 101 + 31 + margen. Con la API sola esto llegó a 162 d; con 150 habría saltado.
    "indice_salarios_total":              150,
    "indice_salarios_registrado":         150,
    "indice_salarios_priv_registrado":    150,
    "indice_salarios_publico":            150,
    "indice_salarios_priv_no_registrado": 150,
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

# Planillas .xls del INDEC. Mismo papel que SERIES_CSV —fuente PRIMARIA, la API de respaldo— para
# las series del comercio exterior. Parser en `source_xls.py`.
#
# POR QUÉ, con la medición del 2026-09-18: el INDEC publicó el ICA de agosto ese día a las 16:00
# y la API seguía cortando en 2026-07. Con la API como primaria, agosto habría entrado recién
# semanas después (julio lo trajo el 16-sep, un mes después de que el INDEC lo publicara).
#
# Contra la API, sobre los 415 meses que comparten (1992-01..2026-07):
#
#     meses idénticos (<1e-6)      : 271 de expo, 270 de impo
#     2005-06, 2014-15, 2018-21    : difieren menos de 0,5 USD M por mes (revisiones chicas)
#     2016, 2017, 2022, 2023       : difieren en serio. Máximo mensual expo/impo, USD M:
#                                    2016 21/59, 2017 7/2, 2022 149/81, 2023 79/36. En el año,
#                                    expo 2022 +258 M y 2023 +167 M en la planilla: la API tiene
#                                    una versión vieja de esos años, la planilla la revisada
#     2026-07 impo                 : 6.738,68 en la API, 6.755,73 en la planilla (revisado con
#                                    el ICA de agosto)
#     sólo en la planilla          : 1990-01..1991-12 y 2026-08
#
# O sea: NO es "el mismo dato por un canal más rápido", como en el índice de salarios. La
# planilla es la publicación VIGENTE del INDEC y la API arrastra una versión anterior de varios
# años. Eso refuerza la prioridad, pero tiene una consecuencia: el cambio de fuente dejó
# snapshots nuevos ('actualizado') en esos meses, y el respaldo no puede volver a pisarlos con el
# número viejo. Ver `del_cuadro` en run.py.
#
# El saldo es la columna 'Saldo' de la planilla, no una resta nuestra. Igual cuadra: saldo =
# expo - impo hasta 1.5e-12 en toda la planilla.
#
# ESTADO: la planilla marca meses provisorios ('*' en el año: todo el año) y estimados ('e' en el
# mes). Esos meses entran con estado='provisorio'; el resto, 'definitivo'. Ver el porqué en run.py.
#
# `columnas`: índice de columna (0-based) -> serie nuestra. `encabezados`: lo que esa columna
# tiene que decir en la fila 'Período'; se valida, para que una columna corrida no cargue
# importaciones como exportaciones. La URL es fija, sin fecha en el nombre, como la del CSV.
SERIES_XLS = {
    "balanza_comercial": {
        "url": "https://www.indec.gob.ar/ftp/cuadros/economia/balanmensual.xls",
        "columnas": {2: "expo_total", 7: "impo_total", 11: "saldo_total"},
        "encabezados": {2: "Exportaciones", 7: "Importaciones", 11: "Saldo"},
        # Se valida contra el subencabezado de cada columna: si el INDEC cambiara de escala sin
        # tocar los títulos, todos los valores se correrían 1000x sin que nada lo note.
        "unidad": "Millones de dólares",
    },
}

XLS_POR_SERIE = {serie: nombre
                 for nombre, cuadro in SERIES_XLS.items()
                 for serie in cuadro["columnas"].values()}

# serie -> URL de su cuadro primario (CSV o XLS). Lo usa run.py para no pisar con la API lo que
# ya escribió el cuadro.
CUADRO_POR_SERIE = {
    **{s: SERIES_CSV[n]["url"] for s, n in CSV_POR_SERIE.items()},
    **{s: SERIES_XLS[n]["url"] for s, n in XLS_POR_SERIE.items()},
}

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
