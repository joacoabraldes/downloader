"""Fuente ADEFA: descarga + parseo del informe mensual (PDF) de la industria automotriz.

El "Informe de prensa" mensual de ADEFA es un PDF con 3 series que nos interesan, cada
una como serie aparte (en unidades):
  - produccion  -> "Producción Nacional"   (la "Hoja1 Columna B" del Excel de referencia)
  - expo        -> "Exportaciones"
  - ventas      -> "Ventas a Concesionarios" (ventas mayoristas)

URL del PDF (resuelta inspeccionando los botones de descarga de la página, que los
genera por JS):
    https://www.adefa.org.ar/upload/estadisticas/resumen-<YYYY>-<MM>-es.pdf

Parseo (pdfplumber): la página "Comparativo" del PDF trae una fila por serie con las
columnas [mes anterior, MES DEL INFORME, var%, mismo mes año anterior, var%, acum...].
Tomamos el 2º valor entero de cada fila = el mes del informe. Ej. (Mayo 2026):
    Producción Nacional  37.521  37.762  0,6% 48.109 -21,5% 207.630 167.629 -19,3%
                          ^Abr    ^May(=el que queremos)
"""
from __future__ import annotations

import datetime as dt
import io
import re

import requests

BASE = "https://www.adefa.org.ar/upload/estadisticas"
HEADERS = {"User-Agent": "Mozilla/5.0 (automotriz ETL)"}
TIMEOUT = 90

# Token "valor en unidades": entero con punto de miles, sin coma ni % (descarta los
# porcentajes tipo "0,6%" / "-21,5%"; los acumulados se filtran por posición).
_UNIT = re.compile(r"^-?\d{1,3}(?:\.\d{3})*$")

# Etiqueta de fila por serie en la página "Comparativo" (la 'ó' viene como mojibake en
# algunos PDFs, por eso 'Producci.n').
_LABELS = {
    "produccion": re.compile(r"Producci.n\s+Nacional", re.IGNORECASE),
    "expo": re.compile(r"^Exportaciones\b", re.IGNORECASE),
    "ventas": re.compile(r"Ventas\s+a\s+Concesionarios", re.IGNORECASE),
}


def pdf_url(year: int, month: int) -> str:
    return f"{BASE}/resumen-{year:04d}-{month:02d}-es.pdf"


def download_pdf(year: int, month: int) -> bytes | None:
    """Baja el PDF del mes. None si no está publicado (404). verify=False: cert de ADEFA."""
    resp = requests.get(pdf_url(year, month), headers=HEADERS, timeout=TIMEOUT,
                        verify=False)
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    return resp.content


def parse_pdf(pdf_bytes: bytes, year: int, month: int) -> dict | None:
    """Extrae {produccion, ventas, expo} (float) del mes desde la tabla 'Comparativo'."""
    import pdfplumber  # import perezoso: solo automotriz lo necesita

    out: dict[str, float] = {}
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            text = page.extract_text() or ""
            if "Comparativo" not in text:
                continue
            for line in text.splitlines():
                stripped = line.strip()
                for serie, rx in _LABELS.items():
                    if serie in out or not rx.search(stripped):
                        continue
                    units = [t for t in stripped.split() if _UNIT.match(t)]
                    if len(units) >= 2:  # [mes anterior, MES DEL INFORME, ...]
                        out[serie] = float(int(units[1].replace(".", "")))
    return out or None


def get_month(year: int, month: int) -> dict | None:
    """Devuelve {'produccion','ventas','expo'} del mes, o None si no está publicado."""
    pdf_bytes = download_pdf(year, month)
    if not pdf_bytes:
        return None
    return parse_pdf(pdf_bytes, year, month)


if __name__ == "__main__":  # smoke test
    import urllib3
    urllib3.disable_warnings()
    today = dt.date.today()
    print(get_month(today.year, today.month))
