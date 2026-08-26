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

# Por ahora una sola serie: la CANTIDAD de actos del mes.
#
# El informe trae además, en el mismo texto, el monto involucrado, la cantidad de escrituras
# con hipoteca y el monto medio (en pesos y en dólares). Quedan afuera a propósito: sumarlas
# es agregar un regex en `source.py` y un nombre acá, pero el monto en pesos corrientes bajo
# inflación argentina no se compara mes contra mes sin deflactar, y eso es una decisión aparte.
SERIES = ["compraventa"]
MAIN_SERIE = "compraventa"
