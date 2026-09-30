"""Dataset BCBA: Panorama Agrícola Semanal (Bolsa de Cereales de Buenos Aires).

Estimación semanal por zona PAS (I a XV), nacional (suma de zonas) y la estimación nacional
oficial ("Actual") de soja, maíz, trigo, girasol, cebada y sorgo, desde 2009/10: área sembrada,
avance de siembra y cosecha, área perdida, rinde y producción. Fuente: tablero público de Power BI
de la BCBA. Grano: campaña agrícola, con vintages semanales (append-only). Sin desest ni deflación.

No confundir con las estimaciones del MAGyP (estimaciones_agricolas / _semanal / _mensual): son
otra metodología. Se comparan en la vista `estimaciones_actual`, nunca se suman.
"""
