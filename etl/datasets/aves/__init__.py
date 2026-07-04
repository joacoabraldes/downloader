"""Sector avícola (MAGyP): faena de aves mensual.

Serie mensual de faena avícola (en miles de cabezas), publicada por MAGyP (Área Avícola, con
datos de SENASA). Histórico profundo desde 1981 (Excel de referencia); el incremental sale
del Excel "Indicadores de Oferta y Demanda" que MAGyP actualiza cada mes. Modelo long: una
fila por (serie, date, estado). Se desestacionaliza con X-13 (calibrado contra la referencia).

Hoy la única serie es `faena`; la fuente de indicadores trae además producción, comercio y
consumo, que podrían sumarse como series del dataset más adelante.
"""
