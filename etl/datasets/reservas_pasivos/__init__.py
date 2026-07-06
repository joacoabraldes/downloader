"""Dataset BCRA: reservas internacionales y principales pasivos (serie diaria).

Es el primer dataset DIARIO del repo (los otros 9 son mensuales). Se lo mantiene en un carril
separado: date es la fecha real del dato (no el primer día del mes), no se desestacionaliza, y
el consumo transversal va por la vista `series_diarias_actual` (no por `series_actual`, que es
mensual). Ver INTEGRATION.md, sección "Series diarias".
"""
