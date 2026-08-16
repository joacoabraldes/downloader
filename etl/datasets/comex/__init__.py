"""Índices de comercio exterior del INDEC (ICA): valor, precio y cantidad, base 2004=100.

Dos planillas mensuales del INDEC, mismo layout y misma hoja:
  - `serie_mensual_indices_expo.xls`  exportaciones por grandes rubros (nivel general,
    productos primarios, MOA, MOI, combustibles y energía).
  - `serie_mensual_indices_comex.xls` exportaciones e importaciones, sólo nivel general.

De la primera salen las 15 series de exportación; de la segunda, sólo las 3 de importación
(su bloque de exportaciones es idéntico al nivel general de la otra y se usa como chequeo
cruzado, no se ingesta dos veces).

Formato long: una fila por (serie, date, estado). Histórico completo desde 2004-01 en cada
descarga. Se desestacionalizan con X-13 las 6 series de CANTIDAD (5 de expo + 1 de impo):
son el volumen físico despachado, que es lo que se compara mes contra mes.
"""
