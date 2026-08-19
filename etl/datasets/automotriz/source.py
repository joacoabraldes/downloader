"""Fuente ADEFA: descarga + parseo del informe mensual (PDF) de la industria automotriz.

El "Informe de prensa" mensual de ADEFA es un PDF con 3 series que nos interesan, cada
una como serie aparte (en unidades):
  - produccion  -> "Producción Nacional"   (la "Hoja1 Columna B" del Excel de referencia)
  - expo        -> "Exportaciones"
  - ventas      -> "Ventas a Concesionarios" (ventas mayoristas)

## Dos canales, el MISMO PDF

1. ESTADISTICAS (primario)
       https://www.adefa.org.ar/upload/estadisticas/resumen-<YYYY>-<MM>-es.pdf
   URL predecible: el mes está en la ruta, así que pedir un mes no publicado da 404.
   (Resuelta inspeccionando los botones de descarga de la página, que los genera por JS.)

2. PRENSA (respaldo)
       https://www.adefa.org.ar/es/prensa-archivo?id=<N>
   ADEFA sube el informe a la sección de gacetillas ANTES que a estadísticas: al 14/08/2026
   julio ya estaba en prensa y `resumen-2026-07-es.pdf` seguía dando 404. Es el mismo PDF con
   los MISMOS números — verificado 9 de 9 valores idénticos (produccion/ventas/expo) contra
   estadísticas en abril, mayo y junio de 2026.

   **El `id` es un contador de gacetillas, NO uno por mes.** Entre el informe de octubre (288)
   y el de noviembre (294) de 2025 hay cinco ids que son otra cosa, y varios devuelven
   `application/octet-stream` sin ser PDF. Se descubre por HEAD leyendo el `Content-Disposition`
   y matcheando el nombre del mes; **nunca por aritmética** sobre el mes pedido.

## Parseo (pdfplumber)

La página "Comparativo" trae una fila por serie y, arriba, un encabezado que declara el orden
de las columnas. Ej. (informe de julio 2026):

    Jun 2026  Jul 2026  Var. %  Jul 2025  Var. %  Acumulado Acumulado Var. %
    Producción Nacional  37.029  31.189  -15,8%  37.112  -16,0%  287.590  235.847  -18,0%

La columna del mes del informe se ubica **leyendo ese encabezado**, no por posición fija. Antes
se tomaba el 2º valor entero de la fila, que funcionaba por accidente del layout: si ADEFA saca
la columna del mes anterior, el índice 2 pasa a ser `Jul 2025` — el mismo mes del AÑO PASADO, un
número perfectamente plausible que se guardaría como si fuera del año en curso. Nadie se entera.

Por eso `parse_pdf` exige encontrar una columna rotulada con el (mes, año) pedido y, si no la
encuentra, **corta con `FormatoInesperado` en vez de adivinar**. Es la misma decisión que se tomó
en el parser de `aves` con las filas de un solo número.
"""
from __future__ import annotations

import datetime as dt
import io
import re

import requests

from etl.core import http

BASE = "https://www.adefa.org.ar/upload/estadisticas"
PRENSA_BASE = "https://www.adefa.org.ar/es/prensa-archivo"
HEADERS = {"User-Agent": "Mozilla/5.0 (automotriz ETL)"}
TIMEOUT = 90

# Punto de arranque del barrido de gacetillas: el informe de enero-2026, verificado. `run.py`
# pasa el último id ya usado cuando lo hay, así que esto sólo aplica la primera vez.
PRENSA_ID_SEED = 296
# Cuántos ids mirar hacia adelante. Los huecos observados entre informes mensuales llegan a 5;
# 60 deja margen de sobra sin volverse caro (es un HEAD por id, no se baja el cuerpo).
PRENSA_MAX_IDS = 60


class FormatoInesperado(RuntimeError):
    """El PDF no tiene la forma que espera el parser. No se adivina: se corta."""

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

# La fila del encabezado del comparativo es la que trae los "Var. %".
_HEADER = re.compile(r"Var\.\s*%")

# Token de columna del encabezado: "Jun 2026", "Sept 2025".
_COL = re.compile(r"([A-Za-zÁÉÍÓÚáéíóú]{3,10})\.?\s+(20\d{2})")

# Abreviaturas de mes tal como las escribe ADEFA en ese encabezado. Septiembre es **`Sept`**,
# no `Sep` ni `Set`: verificado en el informe de septiembre-2025. Se aceptan las tres variantes
# porque el costo de equivocarse es quedarse sin el mes, no cargar basura.
_MES_ABREV = {
    "ene": 1, "feb": 2, "mar": 3, "abr": 4, "may": 5, "jun": 6,
    "jul": 7, "ago": 8, "sept": 9, "sep": 9, "set": 9,
    "oct": 10, "nov": 11, "dic": 12,
}

# Nombre completo del mes como aparece en el `Content-Disposition` de las gacetillas:
# "INFORME DE PRENSA _ Adefa Julio 2026.pdf", "Resumen de prensa ADEFA noviembre 2025 (v2).pdf".
_MES_NOMBRE = {
    1: "enero", 2: "febrero", 3: "marzo", 4: "abril", 5: "mayo", 6: "junio",
    7: "julio", 8: "agosto", 9: "septiembre", 10: "octubre", 11: "noviembre", 12: "diciembre",
}


def pdf_url(year: int, month: int) -> str:
    return f"{BASE}/resumen-{year:04d}-{month:02d}-es.pdf"


def download_pdf(year: int, month: int) -> bytes | None:
    """Baja el PDF del mes. None si no está publicado (404). verify=False: cert de ADEFA."""
    try:
        resp = http.fetch(pdf_url(year, month), headers=HEADERS, timeout=TIMEOUT,
                          verify=False)
    except requests.HTTPError as e:
        # 404 = el informe del mes todavía no salió. `http.fetch` no reintenta el 404, así que
        # llega en el primer intento y no paga el backoff.
        if e.response is not None and e.response.status_code == 404:
            return None
        raise
    return resp.content


def _columnas(header: str) -> list[tuple[int, int]]:
    """[(mes, anio), ...] en el orden en que el encabezado declara las columnas.

    Se quedan sólo los tokens que son un mes reconocible: "Acumulado" y los "Var. %" quedan
    afuera solos. Los acumulados van después de las columnas mensuales y sus años viven en la
    línea siguiente, así que no contaminan.
    """
    out = []
    for palabra, anio in _COL.findall(header):
        mes = _MES_ABREV.get(palabra.lower().rstrip("."))
        if mes:
            out.append((mes, int(anio)))
    return out


def _indice_columna(header: str, year: int, month: int) -> int:
    """Posición del (mes, año) pedido dentro de las columnas mensuales del encabezado.

    Ese índice sirve para indexar los enteros de cada fila porque los `Var. %` traen coma y `%`
    y `_UNIT` los descarta, con lo que los tres primeros enteros de la fila corresponden 1 a 1
    con las tres primeras columnas mensuales del encabezado.

    Levanta `FormatoInesperado` si no encuentra la columna: eso significa que el PDF no es del
    mes que se pidió (id de gacetilla equivocado) o que ADEFA cambió el layout. En los dos casos
    seguir adelante guardaría un número de otro período como si fuera de éste.
    """
    cols = _columnas(header)
    if len(cols) < 2:
        raise FormatoInesperado(
            f"encabezado del comparativo sin columnas mensuales reconocibles: {header!r}")
    try:
        return cols.index((month, year))
    except ValueError:
        raise FormatoInesperado(
            f"el PDF no tiene columna para {year}-{month:02d}; el encabezado declara "
            f"{[f'{a}-{m:02d}' for m, a in cols]}") from None


def parse_pdf(pdf_bytes: bytes, year: int, month: int) -> dict | None:
    """Extrae {produccion, ventas, expo} (float) del mes desde la tabla 'Comparativo'.

    `year`/`month` NO son decorativos: se usan para ubicar la columna correcta y para verificar
    que el PDF sea efectivamente de ese mes. Devuelve None si no hay tabla comparativo (PDF que
    no es el informe); levanta `FormatoInesperado` si la tabla está pero el mes pedido no.
    """
    import pdfplumber  # import perezoso: solo automotriz lo necesita

    out: dict[str, float] = {}
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            text = page.extract_text() or ""
            if "Comparativo" not in text:
                continue
            lineas = text.splitlines()
            header = next((l for l in lineas if _HEADER.search(l)), None)
            if header is None:
                continue  # la portada de la sección también dice "Comparativo", sin tabla
            idx = _indice_columna(header, year, month)
            for line in lineas:
                stripped = line.strip()
                for serie, rx in _LABELS.items():
                    if serie in out or not rx.search(stripped):
                        continue
                    units = [t for t in stripped.split() if _UNIT.match(t)]
                    if len(units) > idx:
                        out[serie] = float(int(units[idx].replace(".", "")))
    return out or None


def get_month(year: int, month: int) -> dict | None:
    """Devuelve {'produccion','ventas','expo'} del mes, o None si no está publicado."""
    pdf_bytes = download_pdf(year, month)
    if not pdf_bytes:
        return None
    return parse_pdf(pdf_bytes, year, month)


# --------------------------------------------------------------------------------------
# Canal de respaldo: gacetillas de prensa
# --------------------------------------------------------------------------------------

def prensa_url(id_: int) -> str:
    return f"{PRENSA_BASE}?id={id_}"


def prensa_filename(id_: int) -> str | None:
    """Nombre del adjunto de esa gacetilla, por HEAD. None si el id no sirve un adjunto.

    Se usa HEAD a propósito: cada informe pesa ~3,7 MB y el barrido mira decenas de ids. El
    `Content-Disposition` viene igual en la respuesta de HEAD, así que el descubrimiento no
    baja un solo byte de cuerpo.
    """
    resp = requests.head(prensa_url(id_), headers=HEADERS, timeout=TIMEOUT,
                         verify=False, allow_redirects=True)
    if resp.status_code != 200:
        return None
    cd = resp.headers.get("Content-Disposition", "")
    if "filename=" not in cd:
        return None  # los ids que no son adjunto devuelven la página HTML, sin este header
    return cd.split("filename=", 1)[1].strip().strip('"')


def find_prensa(year: int, month: int, *, desde_id: int | None = None,
                max_ids: int = PRENSA_MAX_IDS) -> tuple[int, bytes] | None:
    """Busca la gacetilla del mes barriendo ids hacia adelante. (id, pdf) o None.

    El match es por NOMBRE DE ARCHIVO (mes en letras + año), no por aritmética sobre el id: el
    contador incluye gacetillas que no son el informe mensual, así que `id del mes anterior + 1`
    apuntaría a cualquier cosa. Los nombres además varían de forma ("INFORME DE PRENSA _ Adefa
    Julio 2026.pdf", "Resumen de prensa ADEFA noviembre 2025 (v2).pdf"), por eso se buscan las
    dos piezas sueltas y en minúsculas en vez de matchear un patrón entero.
    """
    objetivo = _MES_NOMBRE[month]
    inicio = max(desde_id or PRENSA_ID_SEED, PRENSA_ID_SEED)
    for id_ in range(inicio, inicio + max_ids):
        nombre = prensa_filename(id_)
        if not nombre or not nombre.lower().endswith(".pdf"):
            continue
        low = nombre.lower()
        if objetivo not in low or str(year) not in low:
            continue
        resp = http.fetch(prensa_url(id_), headers=HEADERS, timeout=TIMEOUT, verify=False)
        # Hay ids que declaran `application/octet-stream` y no son PDF: se chequea el magic
        # byte en vez de confiar en el Content-Type.
        if resp.content[:4] != b"%PDF":
            continue
        return id_, resp.content
    return None


def get_month_prensa(year: int, month: int, *,
                     desde_id: int | None = None) -> tuple[dict, str] | None:
    """({'produccion','ventas','expo'}, url) del mes desde la gacetilla, o None si no está.

    Doble guarda contra cargar el mes equivocado, que es EL riesgo de este canal (el id es
    opaco, no dice de qué mes es): primero el nombre del archivo tiene que nombrar al mes, y
    después `parse_pdf` exige que el encabezado del comparativo declare ese (mes, año). Si el
    barrido se equivocó de id, la segunda guarda levanta `FormatoInesperado`.
    """
    hit = find_prensa(year, month, desde_id=desde_id)
    if not hit:
        return None
    id_, pdf_bytes = hit
    data = parse_pdf(pdf_bytes, year, month)
    if not data:
        return None
    return data, prensa_url(id_)


if __name__ == "__main__":  # smoke test
    import urllib3
    urllib3.disable_warnings()
    today = dt.date.today()
    print(get_month(today.year, today.month))
