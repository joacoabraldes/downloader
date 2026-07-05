"""Demanda energética no residencial (CAMMESA).

Serie mensual de demanda eléctrica NO residencial del MEM argentino, en GWh. La fuente es el
Excel "Demanda Mensual" que publica CAMMESA (hoja única 'DEMANDA', en MWh, tabla horizontal
por tipo de demanda). El incremental sale de ese Excel (2023-01→, se re-publica entero cada
mes); el histórico profundo (2005-01→2022-12) se carga del Excel de referencia energia.xlsx.

La serie principal `no_residencial` = 'Demanda No Residencial' + 'DEMANDA NO ESTACIONALIZADA'
(los grandes usuarios GUDI/GUME/GUMA/MATE, todos no residenciales). Se guardan además todas
las demás filas del cuadro como series propias. Modelo long: una fila por (serie, date, estado).
Se desestacionaliza `no_residencial` con X-13 (add + td1coef + s3x5, calibrado 0.00% vs energia.xlsx).
"""
