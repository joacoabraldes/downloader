"""Fuente Superset de la Secretaría de Energía: producción mensual de petróleo y de gas.

`estadisticas.energia.gob.ar` corre Apache Superset con la API REST ABIERTA (sin login y sin
CSRF), así que no hace falta scrapear el dashboard ni simular el botón de descarga que ofrecen
los tres puntos de cada panel. Un GET por serie devuelve el CSV ya agregado:

    GET /api/v1/chart/<slice_id>/data/?format=csv

Los `slice_id` salen de `GET /api/v1/dashboard/<id>/charts`, que expone el `form_data` guardado
de cada panel. Los dos que sirven son los de la serie mensual por tipo de recurso:

    petroleo   dashboard 68  ->  slice 480   "MGv4_Prod.P.CyNoCm3:año-mes"
    gas        dashboard 74  ->  slice 492   "MGv4_Prod.GAS-Con y no Con(m3).:"

La respuesta trae una fila por mes y una columna por `concepto`:

    indice_tiempo,Producción convencional,Shale Oil,Tight_oil
    2026-07,1292626.2095,3175041.7612,22790.2552

## UNIDADES (la trampa de esta fuente)

La columna del dataset de gas se llama `cantidad_mm3` y la del de petróleo `cantidad_m3`, pero
**las dos vienen en miles de m3**. Dividir por 1000 lleva a las unidades en que se publica cada
serie:

    petroleo -> miles de m3          gas -> millones de m3 (mm3)

Verificado contra el histórico: julio-2026 de petróleo suma 1292626,2095 + 3175041,7612 +
22790,2552 = 4.490.458,2, y el Excel de referencia trae 4490,4582259. Exacto al decimal.

## Por qué el total es la suma de los conceptos

`concepto` es la ÚNICA dimensión de medida del dataset (el resto de las columnas son empresa,
área, cuenca y provincia), así que sumar los conceptos da el total del mes sin doble conteo.
Verificado pidiendo el desagregado por provincia con un POST a `/api/v1/chart/data`: la suma
por provincia coincide con el total del chart con 0,0000% de error.

Si aparece un `concepto` que el mapeo no reconoce, `parse_csv` **corta** con `FormatoInesperado`
en vez de ignorarlo. Ignorarlo subestimaría el total en silencio —el peor error posible acá,
porque el número resultante sigue siendo plausible y nadie se entera—. Es el mismo criterio que
el parser de `automotriz` con la columna del mes.

## Cobertura

Los dos charts arrancan en **2009-01**, que es donde arranca el dato por pozo (declaraciones
juradas del capítulo IV). El tramo 1996-2008 lo cubre el histórico (ver `load_history.py`).

## TLS

El certificado del host está incompleto (falta la cadena intermedia): sin `verify=False` el
request muere con "unable to get local issuer certificate". Mismo caso que ADEFA.
"""
from __future__ import annotations

import csv
import datetime as dt
import io
import re

from etl.core import http

BASE = "https://estadisticas.energia.gob.ar"
HEADERS = {"User-Agent": "Mozilla/5.0 (hidrocarburos ETL)"}
TIMEOUT = 90

# slice_id del panel "año-mes por tipo" de cada dashboard (68 = petróleo, 74 = gas).
CHARTS = {"petroleo": 480, "gas": 492}

# Lo que publica la fuente está en miles de m3 en AMBOS datasets, pese al nombre de la columna
# (`cantidad_m3` en petróleo, `cantidad_mm3` en gas). Dividir por esto deja petróleo en miles
# de m3 y gas en millones de m3.
ESCALA = 1000.0

# Primer mes que cubre la fuente (dato por pozo del capítulo IV).
INICIO = dt.date(2009, 1, 1)

# Columna del índice temporal en el CSV (formato 'YYYY-MM').
COL_TIEMPO = "indice_tiempo"


class FormatoInesperado(RuntimeError):
    """El CSV no tiene la forma que espera el parser. No se adivina: se corta."""


# Mapeo del `concepto` de la fuente al tipo de recurso. Se matchea por substring y no por
# igualdad porque los dos dashboards escriben distinto lo mismo: 'Shale Oil' vs 'Shale gas',
# 'Tight_oil' vs 'Tight gas'.
_TIPOS = (
    ("convencional", re.compile(r"convencional", re.IGNORECASE)),
    ("shale", re.compile(r"shale", re.IGNORECASE)),
    ("tight", re.compile(r"tight", re.IGNORECASE)),
)


def chart_url(serie: str) -> str:
    """URL del CSV del chart de la serie (sirve además como `fuente` de la fila)."""
    return f"{BASE}/api/v1/chart/{CHARTS[serie]}/data/?format=csv"


def _tipo(columna: str) -> str:
    """Tipo de recurso de una columna del CSV, o corta si no se reconoce."""
    for tipo, rx in _TIPOS:
        if rx.search(columna):
            return tipo
    raise FormatoInesperado(
        f"concepto desconocido en el CSV: {columna!r}. Si la fuente agregó un tipo de recurso "
        f"nuevo, sumarlo a _TIPOS y al CHECK de schema.sql; ignorarlo subestimaría el total."
    )


def _mes(valor: str) -> dt.date:
    """'2026-07' -> date(2026, 7, 1). Corta si no tiene esa forma."""
    m = re.fullmatch(r"(\d{4})-(\d{2})", valor.strip())
    if not m:
        raise FormatoInesperado(f"{COL_TIEMPO} con formato inesperado: {valor!r}")
    return dt.date(int(m.group(1)), int(m.group(2)), 1)


def parse_csv(texto: str) -> dict[dt.date, dict[str, float]]:
    """CSV del chart -> {mes: {'total': x, 'convencional': y, 'shale': z, 'tight': w}}.

    Los valores ya vienen divididos por `ESCALA`. Un tipo que la fuente no informa para un mes
    queda ausente del dict (no en 0): 0 es un dato, "no informado" no.
    """
    reader = csv.DictReader(io.StringIO(texto))
    if not reader.fieldnames or COL_TIEMPO not in reader.fieldnames:
        raise FormatoInesperado(
            f"el CSV no trae la columna {COL_TIEMPO!r} (columnas: {reader.fieldnames})")
    # Se resuelve el mapeo ANTES de leer filas: si la fuente agregó un concepto, que corte de
    # entrada y no a mitad del parseo.
    tipos = {col: _tipo(col) for col in reader.fieldnames if col != COL_TIEMPO}
    if not tipos:
        raise FormatoInesperado("el CSV no trae ninguna columna de concepto")

    out: dict[dt.date, dict[str, float]] = {}
    for fila in reader:
        fecha = _mes(fila[COL_TIEMPO])
        datos: dict[str, float] = {}
        for col, tipo in tipos.items():
            crudo = (fila.get(col) or "").strip()
            if crudo == "":
                continue
            datos[tipo] = float(crudo) / ESCALA
        if not datos:
            continue  # mes sin ningún concepto informado: no hay total que calcular
        datos["total"] = sum(datos.values())
        out[fecha] = datos
    if not out:
        raise FormatoInesperado("el CSV no trae ninguna fila con datos")
    return out


def get_serie(serie: str) -> dict[dt.date, dict[str, float]]:
    """Baja y parsea la serie completa (2009-01 → último mes publicado).

    Un solo request trae todo el histórico de la fuente: no hay endpoint "por mes" y pedir el
    CSV entero cuesta ~9 KB, así que el incremental filtra en memoria (ver `run.py`).
    """
    resp = http.fetch(chart_url(serie), headers=HEADERS, timeout=TIMEOUT, verify=False)
    resp.encoding = "utf-8"
    return parse_csv(resp.text)
