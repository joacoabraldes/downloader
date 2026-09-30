"""Config de la tabla etl_estimaciones_agricolas (formato WIDE) para el núcleo genérico.

Grano de la fila: (cultivo, campania, departamento_id). Se eligió wide y no long (a diferencia de
compras_granos) porque las 4 métricas son las mismas desde 1969/70, el rendimiento sólo tiene
sentido al lado de su superficie y su producción, y en long serían ~650 mil filas en vez de ~160.
"""

TABLE = "etl_estimaciones_agricolas"
KEY_COLS = ["cultivo", "campania", "departamento_id"]
# Orden importa: insert_if_changed saltea la fila si el PRIMERO es None. En el release del
# 25/08/2026 ninguna fila tiene valores vacíos (los "SD" viejos pasaron a 0).
VALUE_COLS = ["sup_sembrada", "sup_cosechada", "produccion", "rendimiento"]
# Se escriben con cada snapshot pero no se comparan para el dedup: renombrar un departamento o
# un release nuevo sin cambios en la celda no tiene que generar un snapshot.
EXTRA_COLS = ["provincia_id", "provincia", "departamento", "id_cultivo_fuente",
              "fecha_actualizacion"]
ACTUAL_VIEW = "etl_estimaciones_agricolas_actual"
RELEASES_TABLE = "etl_estimaciones_agricolas_releases"

# Un solo estado: hay una sola fuente y MAGyP revisa celdas viejas entre releases (hasta 1999).
# La vista _actual se queda con el snapshot más reciente, como en compras_granos.
ESTADO = "publicado"

# La tabla no tiene `date`: `etl_control_ejecucion.ultimo_dato` sale de la fecha del último
# release procesado (ver etl.core.control._ultimo_dato). Así `etl_control_salud.estado_dato`
# mide "hace cuánto MAGyP no publica un release", que es la frescura que importa acá.
ULTIMO_DATO_SQL = f"select max(fecha_actualizacion) from {RELEASES_TABLE}"

# Mapeo POR ID de la fuente, nunca por nombre: MAGyP renombra ("Poroto seco" -> "Poroto total")
# pero el id queda. Un id que no está acá aborta la corrida: cultivo nuevo = decisión explícita.
# No hay ids 36 ni 37. Sobre los agregados (NO sumar total con sus partes):
#   soja_total = soja_1ra + soja_2da (desglose desde 2000/01). A nivel nacional cierra con
#     diferencias de redondeo (2023/24: 48.213.216 vs 48.213.227 t); a nivel departamento hay
#     16 celdas que no cierran (release 25/08/2026).
#   cebada_cervecera / cebada_forrajera hasta 2015/16; cebada_total desde 2016/17 (sin solape).
#   trigo_candeal es un subconjunto de trigo_total (~2% en 2023/24): no sumarlos.
#   poroto_total = alubia + negro + otros, desglose sólo 2021/22 -> 2024/25.
#   arveja / garbanzo / lenteja existen desde 2018/19.
CULTIVOS = {
    1: "ajo", 2: "algodon", 3: "alpiste", 4: "arroz", 5: "avena", 6: "banana",
    7: "cana_de_azucar", 8: "cartamo", 9: "cebada_cervecera", 10: "cebada_forrajera",
    11: "cebolla_total", 12: "centeno", 13: "colza", 14: "girasol", 15: "jojoba", 16: "limon",
    17: "lino", 18: "mandarina", 19: "mani", 20: "mijo", 21: "naranja", 22: "papa_total",
    23: "pomelo", 24: "poroto_total", 25: "soja_total", 26: "sorgo", 27: "te",
    28: "trigo_total", 29: "trigo_candeal", 30: "tung", 31: "yerba_mate", 32: "maiz",
    33: "cebada_total", 34: "soja_1ra", 35: "soja_2da", 38: "arveja", 39: "garbanzo",
    40: "lenteja", 41: "poroto_alubia", 42: "poroto_negro", 43: "poroto_otros",
}
