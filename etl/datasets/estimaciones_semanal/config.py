"""Config de etl_estimaciones_semanal (formato LONG).

Grano de la fila: (cultivo, campania, zona_tipo, zona, fase, variable, fecha_corte) -> valor.

`date_informe` NO es parte de la clave (decisión de diseño)
----------------------------------------------------------
Cada informe repite las 6 últimas semanas de % de avance, y MAGyP revisa números entre informes
(maíz 25/26 total país: 11.158.197 ha sembradas al 30/04/2026 -> 11.675.537 al 24/09/2026). Con
`date_informe` en la clave cada informe reescribiría ~3.000 filas aunque nada cambie; fuera de la
clave es una columna de contexto (la fecha del informe en el que apareció ESE valor), igual que
`fecha_actualizacion` en estimaciones_agricolas:

  - append-only con dedup: un valor repetido no genera fila; uno revisado sí, con su
    `date_informe`. La historia de revisiones queda completa;
  - `_actual` = último snapshot de cada clave (por date_informe, después ingested_at);
  - "lo que decía el informe X": el último snapshot con date_informe <= X de cada clave.

Las áreas (sembrada, a sembrar, cosechada, no cosechada) se publican al día del informe: su
`fecha_corte` ES la fecha del informe, así que forman una serie semanal de la estimación.

Celda de % vacía -> NO se escribe fila (NULL por ausencia, no 0)
---------------------------------------------------------------
La fuente deja vacía la celda tanto cuando la labor no empezó como cuando la delegación no
informó; y publica 0 explícito en otros casos (subtotales). Convertir vacío en 0 inventaría un
dato. El consumidor que quiera "sin avance = 0" hace coalesce. Un 0 publicado se guarda como 0.
"""

TABLE = "etl_estimaciones_semanal"
KEY_COLS = ["cultivo", "campania", "zona_tipo", "zona", "fase", "variable", "fecha_corte"]
VALUE_COLS = ["valor"]
# Se escriben con cada snapshot pero no se comparan (date_informe lo agrega el loader).
EXTRA_COLS = ["provincia", "zona_fuente"]
ACTUAL_VIEW = "etl_estimaciones_semanal_actual"
INFORMES_TABLE = "etl_estimaciones_semanal_informes"
TIPO = "semanal"

# La tabla no tiene `date`: ultimo_dato = fecha del último informe procesado (frescura de la
# FUENTE: "hace cuánto MAGyP no publica un semanal").
ULTIMO_DATO_SQL = f"select max(date_informe) from {INFORMES_TABLE}"
