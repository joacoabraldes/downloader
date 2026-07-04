"""Patentamientos 0km (SIOMAA): patentamientos mensuales del mercado 4W, formato long.

Fuente distinta de `automotriz` (ADEFA = producción/ventas/expo de fábrica): acá son
**registraciones** (patentamientos 0km) del "Informe de Mercado 4W" de SIOMAA. Se guarda
una serie por categoría del mercado (autos, comerciales, total, ...), cada una
desestacionalizable con X-13. Modelo long: una fila por (serie, date, estado).
"""
