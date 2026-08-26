"""Config de la tabla etl_escrituras_caba (formato long) para el núcleo genérico (etl.core.db).

Escrituras de compraventa de inmuebles oficializadas por escribanos de la Ciudad Autónoma de
Buenos Aires, sobre inmuebles ubicados en esa misma demarcación (Colegio de Escribanos de la
Ciudad de Buenos Aires).

`_caba` en el nombre no es redundante: hay un Colegio de Escribanos por jurisdicción y el de
la Provincia de Buenos Aires publica su propia serie. Si algún día se suma, va a ser otro
dataset y no una serie más de éste — las jurisdicciones no se agregan entre sí.
"""

TABLE = "etl_escrituras_caba"
KEY_COLS = ["serie", "date"]
VALUE_COLS = ["valor"]
ACTUAL_VIEW = "etl_escrituras_caba_actual"

# Las 5 series salen del MISMO texto del informe mensual.
#
# OJO CON LAS UNIDADES: no las comparten.
#   compraventa      cantidad de actos
#   hipotecas        cantidad de actos formalizados con hipoteca (subconjunto de compraventa)
#   monto            PESOS corrientes (el informe lo publica en millones; se guarda x1e6)
#   monto_medio      PESOS corrientes
#   monto_medio_usd  DÓLARES (al tipo de cambio oficial promedio del mes, según la fuente)
#
# Las dos series en pesos son NOMINALES. Bajo inflación argentina no se comparan mes contra mes
# sin deflactar: eso queda del lado del consumidor (ver `etl_datos_gob_real` para el patrón que
# usa el repo cuando el deflactado es parte del ETL).
#
# `compraventa` es la única con cobertura completa. Las otras cuatro dependen de cómo estaba
# redactado el informe de ese mes y tienen huecos; ver el docstring de `source.py`.
SERIES = ["compraventa", "monto", "hipotecas", "monto_medio", "monto_medio_usd"]
MAIN_SERIE = "compraventa"

# Series secundarias, en el orden en que se reportan. Su ausencia en un mes NO es una falla.
EXTRAS = ["monto", "hipotecas", "monto_medio", "monto_medio_usd"]
