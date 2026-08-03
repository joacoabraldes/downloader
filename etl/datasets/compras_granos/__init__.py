"""Dataset MAGyP: compras de granos (sector exportador / industria) y DJVE, serie SEMANAL.

Segundo dataset no mensual del repo (el otro es `reservas_pasivos`, diario). `date` es la fecha
de corte del informe semanal, no el primer día del mes. No se desestacionaliza: es un mix de
flujos semanales y acumulados de campaña, no una serie mensual comparable con X-13, así que
queda fuera de `series_actual` / `series_desest`.

Ojo: no confundir con el dataset `granos`, que es la molienda de oleaginosas (mensual).
"""
