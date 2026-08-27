"""Fuente Superset (Secretaría de Energía): ventas de derivados al mercado interno.

Sale del mismo servidor que `hidrocarburos` —`estadisticas.energia.gob.ar`, Apache Superset con
la API REST abierta— pero por un camino distinto: acá NO alcanza un chart guardado.

## Por qué POST y no el CSV de un chart

Los charts de los dashboards 4, 5 y 6 traen el TOTAL del mes, sin abrir por producto (el único
que abre, el `slice=31`, es una tabla con `row_limit` y llega truncada). El desagregado se pide
con un `query_context` propio:

    POST /api/v1/chart/data      columns = [indice_tiempo, producto, unidad]

El endpoint acepta la consulta sin login ni CSRF, igual que el GET. Un solo request trae los 51
productos por mes desde 2010-01 (~380 KB), así que no hay endpoint "por mes" ni hace falta: el
incremental filtra en memoria.

## Las tres trampas de este dataset

1. **El mes en curso viene con `0.0`, no ausente.** La fila del mes corriente existe con cero en
   todos los productos. Un ETL ingenuo carga un cero como si fuera dato. Por eso `parse` descarta
   los meses cuyo total es 0: acá un mes entero en cero no es información, es el placeholder.

2. **Hay CRUDO adentro del dataset de ventas.** 15 de los 51 productos son petróleo por cuenca.
   Se ingestan igual (son un dato) pero el catálogo los marca `tipo='crudo'` y quedan fuera de
   todos los totales. Ver `config.py` y `schema.sql`.

3. **Tres unidades conviven en la misma columna `cantidad`**: (m3), (Ton) y (miles/m3). La
   unidad viene como columna aparte del dataset y se guarda en la dimensión; los totales del
   schema filtran por ella. Sumar la columna sin mirar `unidad` mezcla metros cúbicos con
   toneladas — que es exactamente el error que cometí la primera vez que miré esta fuente.

## Producto desconocido: se ingesta Y se avisa

A diferencia de `hidrocarburos`, acá un producto nuevo NO corta la corrida: se guarda igual con
su slug derivado, porque perder el dato es peor. Pero `run.py` lo registra como falla para que la
corrida salga con código != 0. Un refinado nuevo sin clasificar quedaría fuera de los totales y
los subestimaría en silencio, que es lo que no se ve mirando el número.

El certificado TLS del host está incompleto: va con `verify=False`, igual que ADEFA.
"""
from __future__ import annotations

import datetime as dt
import csv
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
HEADERS = {"User-Agent": "Mozilla/5.0 (ventas combustibles ETL)",
           "Content-Type": "application/json"}
TIMEOUT = 120

# Primer mes que cubre la fuente.
INICIO = dt.date(2010, 1, 1)

# Sufijo de unidad pegado al nombre del producto ('Fueloil(Ton)'), que se saca antes de
# normalizar: la unidad ya viene en su propia columna y repetirla en el nombre sólo estorba.
_SUFIJO = re.compile(r"\((?:m3|Ton|miles/m3)\)\s*$", re.IGNORECASE)


class FormatoInesperado(RuntimeError):
    """La respuesta no tiene la forma que espera el parser. No se adivina: se corta."""


def _norm(s: str) -> str:
    """Nombre de producto normalizado: sin sufijo de unidad, sin acentos, sin espacios dobles.

    Se normaliza porque la fuente escribe con errores que un match exacto no perdona:
    'Gasoil Grado 3 (Ultra) (m3)' tiene un espacio de más, 'Santa Cruz - On  Shore' tiene un
    espacio doble y 'Tierra l Fuego' le come el "de".
    """
    s = _SUFIJO.sub("", (s or "").strip())
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", s).strip().lower()


def _slug(nombre: str) -> str:
    """Slug de respaldo para un producto que no está en el catálogo."""
    return re.sub(r"[^a-z0-9]+", "_", _norm(nombre)).strip("_")[:60] or "desconocido"


def query_context() -> dict:
    """El `query_context` del POST. Explícito y en un solo lugar para poder auditarlo."""
    return {
        "datasource": {"id": config.DATASOURCE_ID, "type": "table"},
        "queries": [{
            "columns": ["indice_tiempo", "producto", "unidad"],
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
    """CSV del POST -> ({mes: {serie: valor}}, {productos que no están en el catálogo}).

    Descarta los meses cuyo total es 0: la fuente publica el mes en curso como una fila entera
    de ceros, y guardar eso sería inventar un derrumbe del consumo.
    """
    reader = csv.DictReader(io.StringIO(texto))
    faltan = {"indice_tiempo", "producto", "unidad", "v"} - set(reader.fieldnames or [])
    if faltan:
        raise FormatoInesperado(f"faltan columnas en la respuesta: {sorted(faltan)}")

    meses: dict[dt.date, dict[str, float]] = {}
    desconocidos: set[str] = set()
    for fila in reader:
        m = re.match(r"(\d{4})-(\d{2})", (fila["indice_tiempo"] or "").strip())
        if not m:
            raise FormatoInesperado(f"indice_tiempo inesperado: {fila['indice_tiempo']!r}")
        fecha = dt.date(int(m.group(1)), int(m.group(2)), 1)
        producto = fila["producto"]
        entrada = config.CATALOGO.get(_norm(producto))
        if entrada:
            serie = entrada[0]
        else:
            serie = _slug(producto)
            desconocidos.add(producto)
        crudo = (fila["v"] or "").strip()
        if crudo == "":
            continue
        meses.setdefault(fecha, {})[serie] = float(crudo)

    # El mes en curso llega con todos los productos en cero: es un placeholder, no un dato.
    vacios = [f for f, d in meses.items() if sum(d.values()) == 0]
    for f in vacios:
        del meses[f]
    if not meses:
        raise FormatoInesperado("la respuesta no trae ningún mes con datos")
    return meses, desconocidos


def _post_con_reintentos() -> requests.Response:
    """POST con la MISMA política de reintentos que `etl.core.http`, sin duplicarla.

    `http.fetch` es sólo GET, y este dataset necesita POST para pedir el desagregado por
    producto. En vez de copiar los números de la política (cuántos intentos, cuánto se espera,
    qué códigos se reintentan), se importan de `http`: si mañana se ajusta el backoff del repo,
    esto lo hereda. Los .gob.ar cortan por volumen con 403 y a veces ni completan el handshake;
    las dos fallas son transitorias y las dos se cubren acá.
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
    raise RuntimeError(f"inalcanzable: {API_DATA}")  # el loop sale por return o raise


def get_ventas() -> tuple[dict[dt.date, dict[str, float]], set[str]]:
    """Baja y parsea la serie completa (2010-01 → último mes publicado)."""
    resp = _post_con_reintentos()
    resp.encoding = "utf-8"
    return parse(resp.text)
