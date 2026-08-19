"""Fuente BCRA: baja el Excel diario `diar_bas.xls` y parsea las 37 series.

El archivo (URL fija, formato .xls viejo → se lee con `xlrd`) tiene una hoja `Serie_diaria` con:
  - fila 26 (`cd_serie`): el id de serie del BCRA por columna → es la CLAVE (única y estable).
  - fila 25 (`bas*`): un código corto que NO es único (bas13 aparece 3 veces) → se ignora como clave.
  - filas 15-24: encabezado multi-fila (de acá salió el seed curado de nombres, ver schema.sql).
  - col 0: la fecha (serial de Excel); a partir de la fila 27, una fila por día hábil.

Detección de columnas de datos: el bloque contiguo que arranca en la col 1 y corta en la primera
columna sin `cd_serie`. Eso incluye las 37 series reales y EXCLUYE una columna basura aislada
(cd 37740, en realidad fechas) que aparece más a la derecha tras una banda de columnas vacías.
"""
from __future__ import annotations

import datetime as dt

import xlrd

from etl.core import http

URL = "https://www.bcra.gob.ar/archivos/Pdfs/PublicacionesEstadisticas/diar_bas.xls"
HEADERS = {"User-Agent": "Mozilla/5.0 (reservas_pasivos ETL)"}
TIMEOUT = 120

CD_ROW = 26        # fila con cd_serie por columna
FIRST_DATA_ROW = 27
DATE_COL = 0
# Ancla de "fila publicada": las reservas totales (cd 246) nunca son 0 en 30 años de historia;
# el BCRA carga una fila con casi todo en 0 (solo el tipo de cambio) los días aún sin publicar.
# Si el ancla está en 0/vacía, la fila entera no está publicada y se saltea (los ceros de las
# demás series SÍ son datos reales: p.ej. un monto de vencimiento sin vencimiento ese día).
ANCHOR_CD = "246"


def download(url: str = URL) -> bytes:
    """Baja el archivo (verify=False: el cert de bcra.gob.ar no siempre valida en el server)."""
    r = http.fetch(url, headers=HEADERS, timeout=TIMEOUT, verify=False)
    return r.content


def _serie_cols(sh) -> list[tuple[int, str]]:
    """[(col, cd_serie)] del bloque contiguo de series que arranca en la col 1.

    Corta en la primera columna cuyo `cd_serie` (fila 26) no es numérico. Así toma las 37 series
    y descarta la columna basura aislada que vive detrás de la banda de columnas vacías.
    """
    cols: list[tuple[int, str]] = []
    for c in range(1, sh.ncols):
        cd = sh.cell_value(CD_ROW, c)
        if not isinstance(cd, (int, float)) or cd == "":
            break
        cols.append((c, str(int(cd))))
    return cols


def parse(blob: bytes) -> dict[str, dict[dt.date, float]]:
    """{cd_serie: {date(diaria): valor}} de la hoja `Serie_diaria`.

    La fecha viene como serial de Excel. Se ingesta cada celda numérica; las celdas vacías (una
    serie puede no tener dato para un día) se saltean.
    """
    wb = xlrd.open_workbook(file_contents=blob)
    sh = wb.sheet_by_index(0)
    cols = _serie_cols(sh)
    anchor_col = next((c for c, cd in cols if cd == ANCHOR_CD), None)
    out: dict[str, dict[dt.date, float]] = {cd: {} for _, cd in cols}
    for r in range(FIRST_DATA_ROW, sh.nrows):
        f = sh.cell_value(r, DATE_COL)
        if not isinstance(f, float) or f <= 1000:  # fila sin fecha (trailing / separador)
            continue
        if anchor_col is not None:  # saltear filas no publicadas (reservas totales en 0/vacío)
            av = sh.cell_value(r, anchor_col)
            if not isinstance(av, (int, float)) or av == "" or av == 0:
                continue
        y, m, d = xlrd.xldate_as_tuple(f, wb.datemode)[:3]
        fecha = dt.date(y, m, d)
        for c, cd in cols:
            v = sh.cell_value(r, c)
            if isinstance(v, (int, float)) and v != "":
                out[cd][fecha] = float(v)
    return out


def get_latest() -> tuple[dict[str, dict[dt.date, float]], str] | None:
    """({cd_serie: {date: valor}}, url) del diar_bas.xls, o None si no se parseó nada."""
    data = parse(download(URL))
    return (data, URL) if any(data.values()) else None


if __name__ == "__main__":  # smoke test
    import urllib3
    urllib3.disable_warnings()
    res = get_latest()
    if res:
        data, url = res
        print(url)
        print(f"series: {len(data)}")
        tot = data.get("246", {})
        if tot:
            for d in sorted(tot)[-5:]:
                print(f"  246  {d}  {tot[d]:,.0f}")
