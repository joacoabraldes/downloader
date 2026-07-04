"""Bovinos (MAGyP): producción mensual de carne bovina.

Serie mensual de producción de carne bovina (en miles de toneladas res con hueso), en base a
datos de SENASA. Histórico profundo desde 1998 (Excel de referencia); el incremental sale del
xls "Faena Bovina ... mensual" que MAGyP linkea DENTRO del Tablero de Faena Bovina (PDF).
Modelo long: una fila por (serie, date, estado). Se desestacionaliza con X-13.

La fuente de indicadores trae además faena (cabezas), % hembras, peso y composición, que
podrían sumarse como series del dataset más adelante.
"""
