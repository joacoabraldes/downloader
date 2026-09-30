"""Config de etl_estimaciones_mensual (formato LONG, total país).

Grano: (cultivo, campania, variable) -> valor. `date_informe` es contexto, no clave (mismo
criterio que estimaciones_semanal): un valor que se repite entre informes no genera fila, uno
revisado sí; `_actual` = el del informe más reciente.

Variables: superficie_implantada_ha (también "sembrada" en la fuente), superficie_a_implantar_ha
(la INTENCIÓN, antes de terminar la siembra; en la columna de la campaña anterior ese rótulo se
guarda como implantada), produccion_t y, para maíz,
superficie_grano_ha (destinada a grano cosechada) y superficie_silaje_otros_ha (silajes,
diferidos, pérdida). `origen`: 'estimacion' (columna del mes de la tabla de esa campaña) o
'campania_anterior' (columna de la campaña anterior en la tabla de la siguiente, p.ej. trigo
25/26 en septiembre, cuando su propia tabla ya no se publica).
"""

TABLE = "etl_estimaciones_mensual"
KEY_COLS = ["cultivo", "campania", "variable"]
VALUE_COLS = ["valor"]
EXTRA_COLS = ["origen", "etiqueta"]
ACTUAL_VIEW = "etl_estimaciones_mensual_actual"
INFORMES_TABLE = "etl_estimaciones_mensual_informes"
TIPO = "mensual"

ULTIMO_DATO_SQL = f"select max(date_informe) from {INFORMES_TABLE}"
