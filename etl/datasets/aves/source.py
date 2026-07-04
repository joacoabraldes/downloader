"""Fuente MAGyP: descubre el último Excel de indicadores avícolas y parsea la faena mensual.

La página de "Carne Aviar" no tiene tablas: los datos están en archivos. El más útil es
`Indicadores de Oferta y Demanda 2016-<año>.xlsx`, que trae la faena mensual (col "Faena
SENASA", miles de cabezas) de todos los años en un solo archivo y se actualiza cada mes.

El xlsx está apilado por año: una fila con el año (col A = '2016') y debajo 12 filas de mes
(col A = 'Enero'...). De cada fila de mes tomamos la col B = faena. El link del xlsx cambia
de carpeta/nombre por año, así que se scrapea la página y se elige el de mayor año.
"""
from __future__ import annotations

import datetime as dt
import io
import re
import urllib.parse

import openpyxl
import requests
from bs4 import BeautifulSoup

from . import config

PAGE = "https://www.magyp.gob.ar/sitio/areas/aves/estadistica/carne/index.php"
HEADERS = {"User-Agent": "Mozilla/5.0 (aves ETL)"}
TIMEOUT = 90

_MESES = {"enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6,
          "julio": 7, "agosto": 8, "septiembre": 9, "setiembre": 9, "octubre": 10,
          "noviembre": 11, "diciembre": 12}
FAENA_COL = 2  # col B del xlsx = "Faena SENASA" (miles de cabezas)


def find_latest_indicadores_xlsx() -> str | None:
    """URL absoluta del 'Indicadores de Oferta y Demanda' .xlsx de mayor año, o None."""
    r = requests.get(PAGE, headers=HEADERS, timeout=TIMEOUT, verify=False)
    r.raise_for_status()
    soup = BeautifulSoup(r.content, "lxml")
    best = None
    for a in soup.find_all("a", href=True):
        href = a["href"]
        name = href.rsplit("/", 1)[-1].lower()
        if "indicadores de oferta y demanda" not in name or not name.endswith(".xlsx"):
            continue
        years = [int(y) for y in re.findall(r"\d{4}", name)]
        key = max(years) if years else 0
        if best is None or key > best[0]:
            best = (key, urllib.parse.urljoin(PAGE, urllib.parse.quote(href)))
    return best[1] if best else None


def download(url: str) -> bytes:
    """Baja el archivo (verify=False: el cert de magyp.gob.ar no valida en el server)."""
    r = requests.get(url, headers=HEADERS, timeout=TIMEOUT, verify=False)
    r.raise_for_status()
    return r.content


def parse_faena(xlsx_bytes: bytes) -> dict[dt.date, float]:
    """{date(primer día del mes): faena} desde el xlsx apilado por año."""
    wb = openpyxl.load_workbook(io.BytesIO(xlsx_bytes), data_only=True)
    ws = wb["Hoja1"] if "Hoja1" in wb.sheetnames else wb[wb.sheetnames[0]]
    out: dict[dt.date, float] = {}
    year = None
    for r in range(1, ws.max_row + 1):
        a = ws.cell(r, 1).value
        if a is None:
            continue
        s = str(a).strip()
        if s.isdigit() and len(s) == 4:  # fila de año -> abre el bloque
            year = int(s)
            continue
        mes = _MESES.get(s.lower())
        b = ws.cell(r, FAENA_COL).value
        if mes and year and b is not None:
            out[dt.date(year, mes, 1)] = float(b)
    wb.close()
    return out


def get_latest() -> tuple[dict[dt.date, float], str] | None:
    """({date: faena}, url) del último xlsx de indicadores, o None si no se encontró."""
    url = find_latest_indicadores_xlsx()
    if not url:
        return None
    return parse_faena(download(url)), url


if __name__ == "__main__":  # smoke test
    import urllib3
    urllib3.disable_warnings()
    res = get_latest()
    if res:
        data, url = res
        print(url)
        for d in sorted(data)[-6:]:
            print(f"  {d:%Y-%m}  {data[d]}")
