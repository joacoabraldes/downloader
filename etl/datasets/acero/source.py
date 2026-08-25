"""Fuente CAA: descubre el último PDF de "Cifras" y parsea la serie de acero crudo.

Los datos están sólo en PDFs (la página no tiene tablas). Hay dos familias: los
`Cifras-<mes>-<año>.pdf` (la tabla de producción, la que sirve) y los `CAA-INFORME-*`
(prosa). El nombre del PDF de cifras es inconsistente (`cifrasenero2026-sin-grafico.pdf`,
`Cifras-mayo-2026.pdf`, ...), así que hay que scrapear la página y quedarse con el más
nuevo (mayor año/mes parseado del nombre), no se puede construir la URL.

Cada PDF trae los últimos ~13 meses. El texto extrae limpio y en orden: cada fila mensual
es `<Mes> <Año>` seguido de 8 valores (arrabio, esponja, total hierro primario, ACERO CRUDO,
planos 1, planos 2, total laminados, planos en frío). Tomamos la 4ª (acero crudo).

El descubrimiento es por link, y eso tiene un punto ciego: la CAA sube el PDF a
`wp-content/uploads/<año>/<mes de subida>/` y a veces tarda días en linkearlo en la página.
Mientras tanto el archivo ya se baja por ruta directa, pero `find_latest_cifras_pdf()` no lo
ve. Pasó con julio 2026: subido el 2026-08-17, cargado a mano el 2026-08-25 con la página
todavía sin el link (`.../uploads/2026/08/Cifras-julio2026.pdf`). No se puede construir la URL
para adelantarse — el nombre tuvo 4 convenciones distintas en 6 archivos, y la carpeta es el
mes de subida, no el del dato. `run.py` avisa si el PDF linkeado quedó atrás del último dato
cargado; ante esa señal, revisar a mano si hay un PDF más nuevo sin linkear.
"""
from __future__ import annotations

import datetime as dt
import io
import re

from bs4 import BeautifulSoup

from etl.core import http
from . import config

COMUNICADOS_URL = "https://www.acero.org.ar/comunicados-cifras-2023-2-2-2/"
HEADERS = {"User-Agent": "Mozilla/5.0 (acero ETL)"}
TIMEOUT = 90

_MESES = {"enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6,
          "julio": 7, "agosto": 8, "septiembre": 9, "setiembre": 9, "octubre": 10,
          "noviembre": 11, "diciembre": 12}
# Mes + año en el nombre del PDF (admite separadores o pegado: 'cifrasenero2026', 'Cifras-mayo-2026').
_NAME_RX = re.compile(r"(" + "|".join(_MESES) + r")[^0-9]*(\d{4})", re.IGNORECASE)
# Fila mensual del PDF: "<Mes> <Año> <8 valores con coma decimal>".
_ROW_RX = re.compile(r"^([A-Za-zÁÉÍÓÚáéíóúÑñ]+)\s+(\d{4})\s+(.+)$")
_NUM_RX = re.compile(r"-?\d[\d.]*,\d+")

ACERO_IDX = config.PDF_COLS.index("acero_crudo")  # 4ª columna (índice 3)


def _num(t: str) -> float:
    """'1.432,0' -> 1432.0 ; '151,6' -> 151.6 (punto de miles, coma decimal)."""
    return float(t.replace(".", "").replace(",", "."))


def find_latest_cifras_pdf() -> tuple[str, int, int] | None:
    """(url, año, mes) del PDF de Cifras más nuevo publicado en la página, o None."""
    r = http.fetch(COMUNICADOS_URL, headers=HEADERS, timeout=TIMEOUT, verify=False)
    soup = BeautifulSoup(r.content, "lxml")
    best = None
    for a in soup.find_all("a", href=True):
        href = a["href"]
        name = href.rsplit("/", 1)[-1].lower()
        if "cifras" not in name or not name.endswith(".pdf"):
            continue
        m = _NAME_RX.search(name)
        if not m:
            continue
        year, month = int(m.group(2)), _MESES[m.group(1).lower()]
        if best is None or (year, month) > (best[1], best[2]):
            best = (href, year, month)
    return best


def download_pdf(url: str) -> bytes:
    """Baja el PDF (verify=False: el cert de acero.org.ar no valida en el server)."""
    r = http.fetch(url, headers=HEADERS, timeout=TIMEOUT, verify=False)
    return r.content


def parse_cifras(pdf_bytes: bytes) -> dict[dt.date, float]:
    """{date(primer día del mes): acero_crudo} de todas las filas mensuales del PDF."""
    import pdfplumber  # import perezoso

    out: dict[dt.date, float] = {}
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        text = "\n".join((p.extract_text() or "") for p in pdf.pages)
    for line in text.splitlines():
        m = _ROW_RX.match(line.strip())
        if not m:
            continue
        mes = _MESES.get(m.group(1).lower())
        if not mes:  # descarta filas de totales anuales ("2019 831,3 ...") y variaciones
            continue
        nums = _NUM_RX.findall(m.group(3))
        if len(nums) < len(config.PDF_COLS):
            continue
        out[dt.date(int(m.group(2)), mes, 1)] = _num(nums[ACERO_IDX])
    return out


def get_latest() -> tuple[dict[dt.date, float], str] | None:
    """({date: acero_crudo}, url) del último PDF de Cifras, o None si no se encontró."""
    found = find_latest_cifras_pdf()
    if not found:
        return None
    url, _, _ = found
    return parse_cifras(download_pdf(url)), url


if __name__ == "__main__":  # smoke test
    import urllib3
    urllib3.disable_warnings()
    res = get_latest()
    if res:
        data, url = res
        print(url)
        for d in sorted(data):
            print(f"  {d:%Y-%m}  {data[d]}")
