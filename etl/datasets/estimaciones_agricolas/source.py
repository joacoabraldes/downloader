"""Fuente: portal de Estimaciones Agrícolas de MAGyP (datosestimaciones.magyp.gob.ar).

No hay API ni URL de descarga directa. La base completa (todas las campañas desde 1969/70, todos
los cultivos, todos los departamentos) sale respondiendo el formulario PHP del portal:

    POST https://datosestimaciones.magyp.gob.ar/reportes.php?reporte=Estimaciones
    body  Dataset=Dataset
    ->    200 application/csv, ~11,5 MB, ~163 mil filas, UN solo request

El POST filtrado (por cultivo/provincia) devolvió 500 en la investigación: se baja todo o nada.

Señal de release: el POST manda no-cache y no trae ETag ni Last-Modified. La única pista es la
línea "Fecha de Actualización: dd/mm/aaaa" de la página (GET de la misma URL), así que la corrida
hace primero ese GET y sólo baja el CSV si la fecha es nueva.

Formato del CSV (portal, 2026):
  - ISO-8859-1, separador `;`, textos entre comillas, empieza con una línea en blanco.
  - Encabezado fijo (ver ENCABEZADO). El formato cambió en cada release histórico (el CKAN de
    datos.magyp usa otro), así que se valida EXACTO y ante cualquier diferencia se aborta.
  - Ids sin padding: provincia 6, departamento 854 -> INDEC '06854' (zfill(2) + zfill(3)).
  - Campaña '2025/26'. Nombres en mayúsculas sin tildes.
"""
from __future__ import annotations

import csv
import datetime as dt
import re

from etl.core import http

URL = "https://datosestimaciones.magyp.gob.ar/reportes.php?reporte=Estimaciones"
FORM = {"Dataset": "Dataset"}
ENCODING = "iso-8859-1"

ENCABEZADO = [
    "ID Provincia", "Provincia", "ID Departamento", "Departamento", "Id Cultivo", "Cultivo",
    "ID Campaña", "Campana", "Sup. Sembrada (Ha)", "Sup. Cosechada (Ha)", "Producción (Tn)",
    "Rendimiento (Kg/Ha)",
]

# Un request por corrida y sin reintentos: el host lo comparten otros 5 ETLs desde esta IP, y la
# corrida es semanal. Si falla, falla (mail) y la próxima semana vuelve a intentar.
TIMEOUT_PAGINA = 60
TIMEOUT_CSV = 300

_RE_FECHA = re.compile(r"Fecha de Actualizaci(?:ó|&oacute;|o)n:\s*(?:</strong>)?\s*(\d{2})/(\d{2})/(\d{4})")
_RE_CAMPANIA = re.compile(r"^\d{4}/\d{2}$")


class FormatoDesconocido(Exception):
    """El CSV o la página no tienen la forma esperada: hay que mirarlo, no adivinar."""


def fetch_pagina() -> str:
    resp = http.fetch(URL, timeout=TIMEOUT_PAGINA, reintentos=1)
    resp.encoding = "utf-8"  # la página declara charset=UTF-8
    return resp.text


def fetch_csv() -> bytes:
    return http.fetch(URL, data=FORM, timeout=TIMEOUT_CSV, reintentos=1).content


def parse_fecha_actualizacion(html: str) -> dt.date:
    m = _RE_FECHA.search(html)
    if not m:
        raise FormatoDesconocido("la página no trae 'Fecha de Actualización: dd/mm/aaaa'")
    d, mth, y = (int(g) for g in m.groups())
    return dt.date(y, mth, d)


def _num(s: str) -> float | None:
    """Número de la fuente. Vacío -> None. El cero se guarda tal cual (puede ser 'sin dato')."""
    s = s.strip()
    if s == "":
        return None
    try:
        return float(s.replace(",", "."))
    except ValueError as e:
        raise FormatoDesconocido(f"valor no numérico: {s!r}") from e


def parse_csv(raw: bytes) -> tuple[list[dict], dict]:
    """Filas crudas del CSV (sin mapear cultivo) + contadores de descarte.

    Descarta las filas sin provincia o sin departamento: en el release CKAN 2026-03 eran 8 filas
    de 1979-80 que además duplicaban la clave de otra fila con geografía completa (en el portal
    del 25/08/2026 ya no están, la guarda queda por si vuelven).
    """
    texto = raw.decode(ENCODING)
    lineas = texto.lstrip("\r\n").splitlines()
    if not lineas:
        raise FormatoDesconocido("CSV vacío")
    reader = csv.reader(lineas, delimiter=";", quotechar='"')
    encabezado = next(reader)
    if encabezado != ENCABEZADO:
        raise FormatoDesconocido(f"encabezado distinto al esperado: {encabezado!r}")

    filas: list[dict] = []
    descartes = {"sin_geo": 0}
    for n, r in enumerate(reader, start=2):
        if not r or all(c.strip() == "" for c in r):
            continue
        if len(r) != len(ENCABEZADO):
            raise FormatoDesconocido(f"línea {n}: {len(r)} columnas, se esperaban {len(ENCABEZADO)}")
        prov_id, prov, dep_id, dep, cult_id, _cult, _camp_id, camp = (c.strip() for c in r[:8])
        if not prov_id or not dep_id or not prov or not dep:
            descartes["sin_geo"] += 1
            continue
        if not _RE_CAMPANIA.match(camp):
            raise FormatoDesconocido(f"línea {n}: campaña con formato inesperado: {camp!r}")
        if not prov_id.isdigit() or not dep_id.isdigit() or not cult_id.isdigit():
            raise FormatoDesconocido(f"línea {n}: ids no numéricos: {r[:8]!r}")
        filas.append({
            "provincia_id": prov_id.zfill(2),
            "provincia": prov,
            "departamento_id": prov_id.zfill(2) + dep_id.zfill(3),
            "departamento": dep,
            "id_cultivo_fuente": int(cult_id),
            "campania": camp,
            "sup_sembrada": _num(r[8]),
            "sup_cosechada": _num(r[9]),
            "produccion": _num(r[10]),
            "rendimiento": _num(r[11]),
        })
    return filas, descartes

