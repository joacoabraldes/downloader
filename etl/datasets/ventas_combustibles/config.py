"""Config de etl_ventas_combustibles (formato long) para el núcleo genérico (etl.core.db).

Ventas al mercado interno de productos derivados del petróleo, del dashboard "Ventas (excluye
ventas a empresas del sector)" de la Secretaría de Energía. Es la contracara de
`hidrocarburos`: aquel mide producción de crudo y gas, éste mide qué se comercializa.

## Star-schema, como comex y datos_gob

La tabla de hechos guarda `serie` (un slug plano) y la dimensión `etl_ventas_combustibles_series`
desarma ese slug en producto / unidad / tipo / familia. La dimensión es lo que permite armar
CUALQUIER total con un `where` en vez de tener que decidirlo al ingestar: hoy el total interesante
es gasoil + nafta, mañana puede ser otro, y no queremos rehacer 200 meses de backfill por eso.

## Por qué se guardan los 51 productos y no sólo los que se suman

Porque el agregado se deriva y el grano no. Guardar sólo el total es una decisión irreversible:
recuperarlo después es un backfill. Guardar el grano cuesta 51 series x ~200 meses y deja abierta
cualquier pregunta futura.

## OJO: hay CRUDO adentro del dataset de ventas

15 de los 51 "productos" no son refinados: son petróleo crudo por cuenca (`Cuenca Neuquina -
Neuquen (Medanito)`, etc.) más `Crudo importado`. Están en el MISMO dataset y entran en el total
que muestra el chart oficial: hasta 6,6% del total en 2010, ~1,5% en 2022, y 0 desde 2025-01.

Se guardan igual (son un dato), marcados `tipo='crudo'`, y quedan FUERA de todos los totales. Un
ETL que tomara el total del chart tal cual tendría la serie 2010-2024 inflada y con una tendencia
a la baja falsa, producida por la desaparición del crudo y no por el consumo.

## Unidades: tres, y no se suman entre sí

`(m3)` los líquidos, `(Ton)` los pesados y el GLP, `(miles/m3)` los gaseosos. La unidad vive en la
dimensión, no en el nombre de la serie, y los totales SIEMPRE filtran por unidad.
"""

TABLE = "etl_ventas_combustibles"
KEY_COLS = ["serie", "date"]
VALUE_COLS = ["valor"]
ACTUAL_VIEW = "etl_ventas_combustibles_actual"

# Tabla catálogo/dimensión (mismo patrón que etl_comex_series).
CATALOG_TABLE = "etl_ventas_combustibles_series"

# Dataset de Superset del que sale todo (dashboards 4, 5 y 6 leen de acá).
DATASOURCE_ID = 5
METRICA = "cantidad"

# Catálogo: producto tal como lo publica la fuente -> (serie, unidad, tipo, familia).
#
# La clave es el nombre NORMALIZADO (minúsculas, sin acentos, sin el sufijo de unidad y con los
# espacios colapsados). Se normaliza porque la fuente escribe con errores que un match exacto no
# perdona: `Gasoil Grado 3 (Ultra) (m3)` tiene un espacio de más antes del sufijo,
# `Cuenca Austral - Santa Cruz - On  Shore` tiene un espacio doble y `Tierra l Fuego` le come el
# "de". Ninguno de los tres es un error nuestro y los tres seguirían rompiendo un match literal.
#
# Valor: (serie, unidad, tipo, familia).
#
# tipo:    refinado | crudo | gaseoso
# familia: agrupa los refinados para poder armar totales sin enumerar productos.
CATALOGO = {
    # --- gasoil ---------------------------------------------------------------
    "gasoil grado 1 (agrogasoil)": ("gasoil_g1_agro", "(m3)", "refinado", "gasoil"),
    "gasoil grado 2 (comun)":      ("gasoil_g2_comun", "(m3)", "refinado", "gasoil"),
    "gasoil grado 3 (ultra)":      ("gasoil_g3_ultra", "(m3)", "refinado", "gasoil"),
    "otros tipos de gasoil":       ("gasoil_otros", "(m3)", "refinado", "gasoil"),
    # --- nafta ----------------------------------------------------------------
    "nafta grado 1 (comun)":       ("nafta_g1_comun", "(m3)", "refinado", "nafta"),
    "nafta grado 2 (super)":       ("nafta_g2_super", "(m3)", "refinado", "nafta"),
    "nafta grado 3 (ultra)":       ("nafta_g3_ultra", "(m3)", "refinado", "nafta"),
    "otros tipos de naftas":       ("nafta_otros", "(m3)", "refinado", "nafta"),
    # --- aviación -------------------------------------------------------------
    "aerokerosene (jet)":          ("aerokerosene", "(m3)", "refinado", "aviacion"),
    "aeronaftas":                  ("aeronaftas", "(m3)", "refinado", "aviacion"),
    # --- GLP ------------------------------------------------------------------
    "butano y otros c4":           ("butano_c4", "(Ton)", "refinado", "glp"),
    "propano y otros c3":          ("propano_c3", "(Ton)", "refinado", "glp"),
    # --- pesados --------------------------------------------------------------
    "fueloil":                     ("fueloil", "(Ton)", "refinado", "pesados"),
    "mezclas ifo":                 ("mezclas_ifo", "(Ton)", "refinado", "pesados"),
    "coque":                       ("coque", "(Ton)", "refinado", "pesados"),
    "asfaltos":                    ("asfaltos", "(Ton)", "refinado", "pesados"),
    "destilado de vacio":          ("destilado_vacio", "(m3)", "refinado", "pesados"),
    "otros productos pesados":     ("otros_pesados", "(m3)", "refinado", "pesados"),
    # --- lubricantes ----------------------------------------------------------
    "lubricantes automotrices":    ("lubricantes_automotrices", "(m3)", "refinado", "lubricantes"),
    "lubricantes industriales":    ("lubricantes_industriales", "(m3)", "refinado", "lubricantes"),
    "lubricantes marinos":         ("lubricantes_marinos", "(m3)", "refinado", "lubricantes"),
    "bases lubricantes":           ("bases_lubricantes", "(m3)", "refinado", "lubricantes"),
    "grasas":                      ("grasas", "(Ton)", "refinado", "lubricantes"),
    # --- solventes ------------------------------------------------------------
    # `nafta virgen` va acá y NO en la familia nafta: es materia prima petroquímica, no
    # combustible de surtidor. Meterla en el total automotor lo inflaría con algo que no se
    # quema en un motor.
    "solventes aromaticos":        ("solventes_aromaticos", "(m3)", "refinado", "solventes"),
    "solventes alifaticos":        ("solventes_alifaticos", "(m3)", "refinado", "solventes"),
    "solventes hexano":            ("solventes_hexano", "(m3)", "refinado", "solventes"),
    "aguarras":                    ("aguarras", "(m3)", "refinado", "solventes"),
    "nafta virgen":                ("nafta_virgen", "(m3)", "refinado", "solventes"),
    # --- otros refinados ------------------------------------------------------
    "kerosene":                    ("kerosene", "(m3)", "refinado", "otros"),
    "diesel oil":                  ("diesel_oil", "(m3)", "refinado", "otros"),
    "gasolina natural":            ("gasolina_natural", "(m3)", "refinado", "otros"),
    "otros productos livianos":    ("otros_livianos", "(m3)", "refinado", "otros"),
    "otros productos medianos":    ("otros_medianos", "(m3)", "refinado", "otros"),
    # --- gaseosos -------------------------------------------------------------
    "gas de refineria":            ("gas_refineria", "(miles/m3)", "gaseoso", "gas"),
    "gas natural":                 ("gas_natural", "(miles/m3)", "gaseoso", "gas"),
    "gas natural licuado":         ("gnl", "(miles/m3)", "gaseoso", "gas"),
    # --- crudo (NO entra en ningún total de refinados) -------------------------
    "crudo importado":                                  ("crudo_importado", "(m3)", "crudo", "crudo"),
    "cuenca austral - santa cruz - off shore":          ("crudo_austral_scruz_off", "(m3)", "crudo", "crudo"),
    "cuenca austral - santa cruz - on shore":           ("crudo_austral_scruz_on", "(m3)", "crudo", "crudo"),
    "cuenca austral - tierra l fuego - off shore (hidra)": ("crudo_austral_tdf_off", "(m3)", "crudo", "crudo"),
    "cuenca austral - tierra l fuego - on shore (san sebastian)": ("crudo_austral_tdf_on", "(m3)", "crudo", "crudo"),
    "cuenca cuyana y bolsones":                         ("crudo_cuyana", "(m3)", "crudo", "crudo"),
    "cuenca golfo san jorge - chubut (escalante)":      ("crudo_gsj_chubut", "(m3)", "crudo", "crudo"),
    "cuenca golfo san jorge - santa cruz (canadon seco)": ("crudo_gsj_scruz", "(m3)", "crudo", "crudo"),
    "cuenca neuquina - la pampa (medanito)":            ("crudo_neuquina_lapampa", "(m3)", "crudo", "crudo"),
    "cuenca neuquina - mendoza":                        ("crudo_neuquina_mendoza", "(m3)", "crudo", "crudo"),
    "cuenca neuquina - neuquen (medanito)":             ("crudo_neuquina_neuquen", "(m3)", "crudo", "crudo"),
    "cuenca neuquina - rio negro (medanito)":           ("crudo_neuquina_rionegro", "(m3)", "crudo", "crudo"),
    "cuenca noroeste - formosa":                        ("crudo_noroeste_formosa", "(m3)", "crudo", "crudo"),
    "cuenca noroeste - jujuy":                          ("crudo_noroeste_jujuy", "(m3)", "crudo", "crudo"),
    "cuenca noroeste - salta":                          ("crudo_noroeste_salta", "(m3)", "crudo", "crudo"),
}

# Familias que componen el total de combustibles de surtidor. Excluye `aviacion` a propósito
# (el jet no es consumo automotor) y excluye `solventes`, donde vive la nafta virgen.
FAMILIAS_DEL_TOTAL = ("gasoil", "nafta")

# Serie de referencia del dataset, para el control de "hasta dónde llega el dato".
MAIN_SERIE = "gasoil_g2_comun"

SERIES = sorted({s for s, _, _, _ in CATALOGO.values()})

# Unidad de cada serie, derivada del catálogo. La unidad NO se puede inferir de la familia:
# `pesados` mezcla fueloil/coque/asfaltos en toneladas con destilado de vacío y otros pesados en
# m3, y `lubricantes` mezcla los tres lubricantes en m3 con grasas en toneladas.
UNIDAD_POR_SERIE = {s: u for s, u, _, _ in CATALOGO.values()}
