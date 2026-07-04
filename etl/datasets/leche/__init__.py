"""Lechería (MAGyP): producción nacional de leche mensual.

Serie mensual de producción nacional de leche (en litros), publicada por la Dirección
Nacional de Lechería de MAGyP. Histórico e incremental salen del mismo Excel (`PPV021_PPV022.xlsx`,
URL fija). Modelo long: una fila por (serie, date, estado). Se desestacionaliza con X-13
(calibrado contra la referencia: add + td1coef + s3x5, error ~0).
"""
