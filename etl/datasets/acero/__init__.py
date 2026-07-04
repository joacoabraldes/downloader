"""Producción siderúrgica argentina (Cámara Argentina del Acero): acero crudo mensual.

Serie mensual de producción de acero crudo (en miles de toneladas), publicada por la CAA en
un PDF de "Cifras" mensual. Histórico desde 1993 (Excel de referencia); el incremental sale
del PDF mensual. Modelo long: una fila por (serie, date, estado). Se desestacionaliza con
X-13 (calibrado contra la referencia: mode=add, td1coef, s3x5).
"""
