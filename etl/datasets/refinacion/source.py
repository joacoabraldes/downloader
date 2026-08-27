"""Fuente Superset (Secretaría de Energía): insumos procesados por las refinerías.

Mismo servidor y mismo mecanismo que `ventas_combustibles` —POST a `/api/v1/chart/data` con un
`query_context` propio, porque ningún chart guardado abre el desagregado por concepto sin
truncarlo— pero contra el **dataset 73** (dashboard 96, "Productos procesados (m3)").

    POST /api/v1/chart/data     columns = [indice_tiempo, concepto]

Un solo request trae los 36 conceptos por mes desde 2010-01 (~370 KB).

## El nombre del dashboard miente: son INSUMOS

"Productos procesados" suena a salida y es entrada. Lo que sale de la refinería vive en el
dataset 74 (dashboard 97, "Subproductos obtenidos"). La forma de no confundirlos es mirar los
conceptos: acá dicen `Cuenca Neuquina - Neuquen (Medanito)` y `Biodiesel`; allá, `Gasoil Grado 2
(Común)`.

## Las trampas, las mismas que en ventas_combustibles

1. **El mes en curso viene con `0.0`**, no ausente: `parse` descarta los meses cuyo total es 0.
2. **Nombres con errores de tipeo** en la fuente (`Santa Cruz - On  Shore` con espacio doble,
   `Tierra l Fuego` sin el "de"), así que el match va por nombre normalizado.

Y una propia: la fuente trae `cantidadtoneladas` además de `cantidadm3` para los mismos
conceptos. **Sólo se pide m3.** Mezclar dos medidas de lo mismo en la columna `valor` es la
trampa que ventas_combustibles ya tiene y que acá se evita de entrada.

La columna `rectificado` del dataset vale `False` en las 7.200 filas al 2026-08. Se la deja
afuera: una bandera que nunca se prendió no aporta, y si algún día se prende va a cambiar valores
que el modelo append-only ya captura como snapshot nuevo.

Concepto desconocido: se ingesta igual con un slug derivado (perder el dato es peor) y `run.py`
lo reporta como falla, para que la corrida salga con código != 0. Un concepto de crudo sin
clasificar quedaría fuera de `crudo_procesado` y lo subestimaría en silencio.

El certificado TLS del host está incompleto: va con `verify=False`, igual que ADEFA.
"""
from __future__ import annotations

import csv
import datetime as dt
import io
import json
import re
import time
import unicodedata

import requests

from etl.core import http
from . import config

BASE = "https://estadisticas.energia.gob.ar"
API_DATA = f"{BASE}/api/v1/chart/data"
HEADERS = {"User-Agent": "Mozilla/5.0 (refinacion ETL)",
           "Content-Type": "application/json"}
TIMEOUT = 120

INICIO = dt.date(2010, 1, 1)


class FormatoInesperado(RuntimeError):
    """La respuesta no tiene la forma que espera el parser. No se adivina: se corta."""


def _norm(s: str) -> str:
    """Minúsculas sin acentos y sin espacios dobles (la fuente tiene ambos errores)."""
    s = unicodedata.normalize("NFKD", (s or "").strip())
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", s).strip().lower()


def _slug(nombre: str) -> str:
    """Slug de respaldo para un concepto que no está en el catálogo."""
    return re.sub(r"[^a-z0-9]+", "_", _norm(nombre)).strip("_")[:60] or "desconocido"


def query_context() -> dict:
    """El `query_context` del POST. Explícito y en un solo lugar para poder auditarlo."""
    return {
        "datasource": {"id": config.DATASOURCE_ID, "type": "table"},
        "queries": [{
            "columns": ["indice_tiempo", "concepto"],
            "metrics": [{
                "aggregate": "SUM",
                "column": {"column_name": config.METRICA},
                "expressionType": "SIMPLE",
                "label": "v",
            }],
            "row_limit": 200000,
            "orderby": [],
        }],
        "result_format": "csv",
        "result_type": "full",
    }


def parse(texto: str) -> tuple[dict[dt.date, dict[str, float]], set[str]]:
    """CSV del POST -> ({mes: {serie: valor}}, {conceptos fuera del catálogo})."""
    reader = csv.DictReader(io.StringIO(texto))
    faltan = {"indice_tiempo", "concepto", "v"} - set(reader.fieldnames or [])
    if faltan:
        raise FormatoInesperado(f"faltan columnas en la respuesta: {sorted(faltan)}")

    meses: dict[dt.date, dict[str, float]] = {}
    desconocidos: set[str] = set()
    for fila in reader:
        m = re.match(r"(\d{4})-(\d{2})", (fila["indice_tiempo"] or "").strip())
        if not m:
            raise FormatoInesperado(f"indice_tiempo inesperado: {fila['indice_tiempo']!r}")
        entrada = config.CATALOGO.get(_norm(fila["concepto"]))
        if entrada:
            serie = entrada[0]
        else:
            serie = _slug(fila["concepto"])
            desconocidos.add(fila["concepto"])
        crudo = (fila["v"] or "").strip()
        if crudo == "":
            continue
        meses.setdefault(dt.date(int(m.group(1)), int(m.group(2)), 1), {})[serie] = float(crudo)

    # El mes en curso llega con todos los conceptos en cero: es un placeholder, no un dato.
    for f in [f for f, d in meses.items() if sum(d.values()) == 0]:
        del meses[f]
    if not meses:
        raise FormatoInesperado("la respuesta no trae ningún mes con datos")
    return meses, desconocidos


def _post_con_reintentos() -> requests.Response:
    """POST con la MISMA política de reintentos que `etl.core.http`, sin duplicarla.

    `http.fetch` es sólo GET. En vez de copiar los números de la política se importan de `http`:
    si mañana se ajusta el backoff del repo, esto lo hereda.
    """
    espera = http.ESPERA_BASE
    for intento in range(http.REINTENTOS):
        ultimo = intento == http.REINTENTOS - 1
        try:
            resp = requests.post(API_DATA, headers=HEADERS, data=json.dumps(query_context()),
                                 timeout=TIMEOUT, verify=False)
        except http.ERRORES_DE_RED:
            if ultimo:
                raise
            time.sleep(espera)
            espera *= 2
            continue
        if resp.status_code in http.REINTENTABLES and not ultimo:
            time.sleep(espera)
            espera *= 2
            continue
        resp.raise_for_status()
        return resp
    raise RuntimeError(f"inalcanzable: {API_DATA}")


def get_insumos() -> tuple[dict[dt.date, dict[str, float]], set[str]]:
    """Baja y parsea la serie completa (2010-01 → último mes publicado)."""
    resp = _post_con_reintentos()
    resp.encoding = "utf-8"
    return parse(resp.text)
