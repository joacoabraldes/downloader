"""Config de etl_refinacion (formato long) para el núcleo genérico (etl.core.db).

INSUMOS procesados por las refinerías del país: crudo por cuenca más el resto de las corrientes
que entran al proceso (biocombustibles, cortes, mejoradores de octano). Es el dashboard 96 de la
Secretaría de Energía, "Productos procesados (m3)".

## Ojo con el nombre del dashboard: son INSUMOS, no productos terminados

"Productos procesados" suena a salida y es entrada. Lo que sale de la refinería está en OTRO
dataset de la fuente —el 74, dashboard 97 "Subproductos obtenidos"— con gasoil, nafta, fueloil,
coque y asfaltos. Se distingue mirando los conceptos: acá dicen `Cuenca Neuquina - Neuquen
(Medanito)` y `Biodiesel`; allá dicen `Gasoil Grado 2 (Común)`. Si algún día se suma la salida,
va como dataset aparte y no como series de éste: son dos lados de la misma transformación y
mezclarlos en una tabla invita a sumarlos, que no significa nada.

## Star-schema, igual que ventas_combustibles

Hechos (`serie`, `date`, `valor`) + dimensión `etl_refinacion_series` con tipo / familia. Se
guardan los 36 conceptos y los agregados se derivan en vistas: `crudo_procesado`,
`otros_insumos` y `total_procesado`.

`crudo_procesado` es la serie que se sigue —el "crude run" clásico— y NO es lo mismo que el
total: el crudo era el 85,5% de lo procesado en 2010 y hoy es el 78,9%. La diferencia la
explican los biocombustibles, que crecieron con los cortes obligatorios. Usar el total como si
fuera crudo procesado sobreestima, y cada vez más.

Para el crudo la `familia` es la CUENCA de origen, que sale gratis y deja ver el corrimiento
hacia la Neuquina (Vaca Muerta) en la dieta de las refinerías.

Unidad: metros cúbicos. La fuente trae además `cantidadtoneladas`, que no se ingesta: mezclar
dos medidas de la misma cosa en la columna `valor` es la trampa que ya nos comimos en ventas.
"""

TABLE = "etl_refinacion"
KEY_COLS = ["serie", "date"]
VALUE_COLS = ["valor"]
ACTUAL_VIEW = "etl_refinacion_actual"
CATALOG_TABLE = "etl_refinacion_series"

DATASOURCE_ID = 73
METRICA = "cantidadm3"
UNIDAD = "(m3)"

# Catálogo: concepto de la fuente (NORMALIZADO) -> (serie, tipo, familia).
#
# tipo:    crudo | otro_insumo
# familia: para el crudo, la cuenca de origen; para el resto, el tipo de corriente.
#
# Se normaliza por lo mismo que en ventas_combustibles: la fuente escribe con errores que un
# match exacto no perdona (`Santa Cruz - On  Shore` con espacio doble, `Tierra l Fuego` sin el
# "de"). Ver `source._norm`.
CATALOGO = {
    # --- crudo, por cuenca ----------------------------------------------------
    "cuenca neuquina - neuquen (medanito)":    ("crudo_neuquina_neuquen", "crudo", "neuquina"),
    "cuenca neuquina - rio negro (medanito)":  ("crudo_neuquina_rionegro", "crudo", "neuquina"),
    "cuenca neuquina - la pampa (medanito)":   ("crudo_neuquina_lapampa", "crudo", "neuquina"),
    "cuenca neuquina - mendoza":               ("crudo_neuquina_mendoza", "crudo", "neuquina"),
    "cuenca golfo san jorge - chubut (escalante)": ("crudo_gsj_chubut", "crudo", "golfo_san_jorge"),
    "cuenca golfo san jorge - canadon seco":   ("crudo_gsj_canadon", "crudo", "golfo_san_jorge"),
    "cuenca austral - santa cruz - on shore":  ("crudo_austral_scruz_on", "crudo", "austral"),
    "cuenca austral - santa cruz - off shore": ("crudo_austral_scruz_off", "crudo", "austral"),
    "cuenca austral - tierra l fuego - off shore (hidra)": ("crudo_austral_tdf_off", "crudo", "austral"),
    "cuenca austral - tierra l fuego - san sebastian": ("crudo_austral_tdf_sansebastian", "crudo", "austral"),
    "cuenca noroeste - salta":                 ("crudo_noroeste_salta", "crudo", "noroeste"),
    "cuenca noroeste - formosa":               ("crudo_noroeste_formosa", "crudo", "noroeste"),
    "cuenca noroeste - jujuy":                 ("crudo_noroeste_jujuy", "crudo", "noroeste"),
    "cuenca cuyana y bolsones":                ("crudo_cuyana", "crudo", "cuyana"),
    "crudo importado":                         ("crudo_importado", "crudo", "importado"),
    # --- biocombustibles ------------------------------------------------------
    "biodiesel":                   ("biodiesel", "otro_insumo", "biocombustible"),
    "bioetanol":                   ("bioetanol", "otro_insumo", "biocombustible"),
    # --- cortes ---------------------------------------------------------------
    "cortes de kerosene":          ("cortes_kerosene", "otro_insumo", "corte"),
    "cortes de gas oil":           ("cortes_gasoil", "otro_insumo", "corte"),
    "cortes de nafta virgen":      ("cortes_nafta_virgen", "otro_insumo", "corte"),
    "cortes fuel oil":             ("cortes_fueloil", "otro_insumo", "corte"),
    "cortes de solventes":         ("cortes_solventes", "otro_insumo", "corte"),
    # --- naftas y mejoradores de octano ---------------------------------------
    "nafta virgen":                ("nafta_virgen", "otro_insumo", "nafta_mejorador"),
    "nafta de reformado":          ("nafta_reformado", "otro_insumo", "nafta_mejorador"),
    "nafta de craqueo catalitico": ("nafta_craqueo", "otro_insumo", "nafta_mejorador"),
    "otros tipos de naftas":       ("naftas_otras", "otro_insumo", "nafta_mejorador"),
    "otros mejoradores de octano": ("mejoradores_otros", "otro_insumo", "nafta_mejorador"),
    "mtbe":                        ("mtbe", "otro_insumo", "nafta_mejorador"),
    "gasolina natural":            ("gasolina_natural", "otro_insumo", "nafta_mejorador"),
    # --- otras corrientes -----------------------------------------------------
    "gas natural":                 ("gas_natural", "otro_insumo", "otros"),
    "bases lubricantes":           ("bases_lubricantes", "otro_insumo", "otros"),
    "aditivos para lubricantes":   ("aditivos_lubricantes", "otro_insumo", "otros"),
    "residuo de destilacion":      ("residuo_destilacion", "otro_insumo", "otros"),
    "destilado de vacio":          ("destilado_vacio", "otro_insumo", "otros"),
    "condensado":                  ("condensado", "otro_insumo", "otros"),
    "otros productos":             ("otros_productos", "otro_insumo", "otros"),
}

SERIES = sorted({s for s, _, _ in CATALOGO.values()})

# tipo de cada serie, derivado del catálogo (lo usa el reporte del run para separar crudo del resto).
TIPO_POR_SERIE = {s: t for s, t, _ in CATALOGO.values()}

# Serie de referencia del dataset, para el control de "hasta dónde llega el dato".
MAIN_SERIE = "crudo_neuquina_neuquen"
