"""Transferencias de automotores (DNRPA): trámites de transferencia mensuales, formato long.

Es el mercado de USADOS, y por eso complementa a los otros dos datasets de autos del repo sin
solaparse con ninguno: `automotriz` (ADEFA) es producción/ventas/exportación de FÁBRICA y
`patentamientos` (SIOMAA) son registraciones de 0km. Acá se cuenta el cambio de titular de un
vehículo que ya estaba en el parque.

La fuente es el Boletín Estadístico de la DNRPA, que publica el cuadro anual de trámites por
provincia y mes. Se guarda el TOTAL PAÍS del mes; el desagregado provincial no se persiste
(ver `source.py`). Histórico desde 1995-01.
"""
