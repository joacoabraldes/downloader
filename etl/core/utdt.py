"""Capa de acceso compartida a las series de UTDT (ICC e ICG).

Dos datasets distintos (`icc`, `icg`) bajan de la misma fuente y con la misma mecánica, así
que lo común vive acá y cada `source.py` sólo pone el layout de SU planilla.

Por qué NO se hardcodea la URL del .xls
---------------------------------------
Los links de UTDT son de la forma:

    https://www.utdt.edu/download.php?fname=_178481547035231900.xls

Ese `fname` no es un nombre estable: es el timestamp en microsegundos del momento en que
subieron el archivo (178481547035231900 -> 2026-07-23 14:04:30 UTC, que es exactamente
cuando publicaron el ICC de julio 2026). Cada publicación mensual genera un `fname` nuevo.
Fijar la URL en el código haría que el ETL bajara para siempre la planilla de julio 2026 sin
fallar nunca: se vería sano y no traería un dato nuevo jamás.

Por eso el ETL resuelve el link en cada corrida desde la página de descarga del indicador
(`listado_contenidos.php?id_item_menu=<id>`), que es lo estable.

Fechas
------
Las planillas las editan a mano en Excel de Mac y eso deja dos trampas:

  - Las celdas de fecha llegan como XL_CELL_DATE (ctype 3), no como XL_CELL_NUMBER (2), así
    que hay que aceptar las dos y convertir con el `datemode` del libro (época 1900 vs 1904).
  - El mes recién publicado a veces viene TIPEADO COMO TEXTO ("jul-26") en lugar de como
    fecha. Es justo el mes que el ETL existe para capturar, así que se parsea igual.

Además hay meses que no caen en el día 1 (el ICC trae 2008-02-08, 2009-11-02 y 2009-12-03):
todo se normaliza al primer día del mes, que es el grano real de la serie.
"""
from __future__ import annotations

import datetime as dt
import io
import re
import unicodedata

import requests
import xlrd
from bs4 import BeautifulSoup

from etl.core import meses

BASE = "https://www.utdt.edu"
HEADERS = {"User-Agent": "Mozilla/5.0 (utdt ETL)"}
TIMEOUT = 60

# Sesión compartida: reutiliza la conexión TCP/TLS entre la página y la descarga.
SESSION = requests.Session()
SESSION.headers.update(HEADERS)

# Las celdas de fecha pueden venir como fecha formateada (3) o como serial crudo (2).
CELDAS_FECHA = (xlrd.XL_CELL_NUMBER, xlrd.XL_CELL_DATE)

# Mes tipeado a mano en la planilla ("jul-26", "Jul-2026"). El `[a-z]*` después de la
# abreviatura absorbe la cola de un nombre completo, así que "septiembre-26" también entra.
#
# Las variantes salen de `etl.core.meses`: acá vivía un mapa propio que tenía `sept` y `sep`
# pero NO `set`, con lo que "set-26" no matcheaba y el mes se perdía en silencio. El helper
# ordena además las ramas de más larga a más corta, que es la regla que este módulo había
# descubierto por su cuenta ("'sept' va antes que 'sep' para que el alternador no corte corto
# y deje un 't' colgado") y que ahora está codificada una sola vez para todos los ETL.
_TEXTO_MES = re.compile(
    r"^" + meses.alternancia(formas="cortas") + r"[a-z]*[\s./-]+(\d{2}|\d{4})$")


def normalizar(texto: str) -> str:
    """minúsculas, sin acentos y con los espacios colapsados (para matchear encabezados)."""
    sin_acentos = "".join(c for c in unicodedata.normalize("NFD", texto)
                          if unicodedata.category(c) != "Mn")
    return " ".join(sin_acentos.lower().split())


def resolver_xls(id_item_menu: int) -> str:
    """URL absoluta del .xls publicado hoy en la página de descarga del indicador.

    `id_item_menu` identifica la página ("Serie Histórica ICC", "Descarga de datos" del ICG).
    Esa página puede ofrecer varios formatos (el ICG publica .pdf, .xls y .dta); nos quedamos
    con el .xls, que es el que trae la serie completa.
    """
    url = f"{BASE}/listado_contenidos.php?id_item_menu={id_item_menu}"
    resp = SESSION.get(url, timeout=TIMEOUT)
    resp.raise_for_status()
    sopa = BeautifulSoup(resp.text, "lxml")
    hrefs = []
    for a in sopa.find_all("a", href=True):
        href = a["href"]
        if "download.php" in href and re.search(r"\.xls($|[?&#])", href):
            hrefs.append(href)
    if not hrefs:
        raise RuntimeError(f"no se encontró ningún .xls en {url} (¿cambió la página?)")
    if len(hrefs) > 1:
        # No es fatal, pero conviene enterarse: hasta hoy cada página ofrece un solo .xls.
        print(f"  aviso: {len(hrefs)} .xls en {url}, se usa el primero")
    return requests.compat.urljoin(url, hrefs[0])


def bajar_libro(url: str) -> xlrd.book.Book:
    """Baja el .xls (formato viejo BIFF) y lo abre con xlrd."""
    resp = SESSION.get(url, timeout=TIMEOUT)
    resp.raise_for_status()
    return xlrd.open_workbook(file_contents=resp.content)


def _mes_desde_texto(texto: str) -> dt.date | None:
    """Convierte un mes tipeado a mano ("jul-26", "Jul-2026") en date. None si no matchea."""
    m = _TEXTO_MES.match(normalizar(texto))
    if not m:
        return None
    # "26" -> 2026; el pivote de `anio_2d` sobra acá porque la serie arranca en 1998.
    return dt.date(meses.anio_2d(int(m.group(2))), meses.numero(m.group(1)), 1)


def celda_a_mes(celda, datemode: int) -> dt.date | None:
    """Primer día del mes de una celda de fecha. None si la celda no es una fecha.

    Acepta los tres formatos que aparecen en las planillas de UTDT: fecha real, serial
    numérico y mes tipeado como texto.
    """
    if celda.ctype in CELDAS_FECHA:
        if not celda.value:             # un 0 numérico no es una fecha, es una celda vacía
            return None
        fecha = xlrd.xldate.xldate_as_datetime(celda.value, datemode).date()
        return fecha.replace(day=1)
    if celda.ctype == xlrd.XL_CELL_TEXT and celda.value.strip():
        return _mes_desde_texto(celda.value)
    return None
