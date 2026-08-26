"""Fuente: informes mensuales de escrituras del Colegio de Escribanos de la Ciudad de Buenos Aires.

El sitio es un WordPress y **expone la API REST**, así que no hay que paginar HTML: la
categoría "Estadísticas de escrituras" (id 382) se baja entera en dos requests.

    GET /wp-json/wp/v2/posts?categories=382&per_page=100&page=N

Cada post es el informe de un mes y trae el número en el cuerpo, en negrita:

    Actos de escrituras de compraventa                   6051
    Monto involucrado                                    $ 1.068.467 millones

VERIFICADO contra las planillas que el propio post linkea: los 12 meses del `.xls`
"Comparativo últimos 12 meses" coinciden **exactamente** con lo que dice el texto de los 12
informes correspondientes. El texto es fuente confiable, no un resumen redondeado.

## El mes sale del TÍTULO, nunca de la fecha de publicación

El informe de julio-2026 se publicó el 2026-08-24. Tomar `post.date` como período metería
todos los datos corridos un mes. El título ("...realizadas en julio 2026") es el que manda, y
si no parsea se corta: no se adivina.

## Dos variantes de redacción

Los informes de octubre, noviembre y diciembre de 2019 dicen "Escrituras de compraventa 3265"
en vez de "Actos de escrituras de compraventa". Por eso el "Actos de" del regex es opcional.
Con el patrón estricto esos tres meses se perdían en silencio, que es peor que fallar.

## Los 6 meses sin informe

La fuente nunca publicó informe para 2016-04, 2016-09, 2017-01, 2022-12, 2023-01 y 2023-02.
No son un error de parseo: esos posts no existen en la categoría.

Se recuperan de las planillas **rodantes** que linkea cualquier informe posterior dentro de
los 12 meses siguientes (`...12-meses.xls`, `...cantidad-de-escrituras-por-mes.xls`), que
traen una fila por mes. Entran con `estado='relleno'` para que se distingan del número del
informe propio (ver `schema.sql`).

> Los nombres de esas planillas cambian entre informes (`12-meses`, `ultimos-12-meses`,
> `cantidad_de_escrituras_por_mes`, `12-meses-2022-2023`...), así que **no se construye la URL
> por aritmética**: se leen los `<a href>` del post y se filtran por patrón. Es la misma
> lección que las gacetillas de ADEFA en `automotriz`.

## Ojo con las planillas

- La fila de julio-2026 del `.xls` trae la fecha como el texto **`juk-26`** (typo de la
  fuente, por `jul-26`). Por eso el lector de planillas ignora las filas cuya primera celda no
  es una fecha real de Excel, en vez de intentar interpretar el string.
- Son `.xls` legacy (OLE2), no xlsx: se leen con `xlrd`, igual que `comex`.
"""
from __future__ import annotations

import datetime as dt
import re
import unicodedata

import xlrd
from bs4 import BeautifulSoup

from etl.core import http

BASE = "https://www.colegio-escribanos.org.ar"
API = f"{BASE}/wp-json/wp/v2/posts"
CATEGORIA = 382          # "Estadísticas de escrituras"
PER_PAGE = 100
MAX_PAGINAS = 20         # tope de seguridad; hoy la categoría entra en 2 páginas
TIMEOUT = 60
HEADERS = {"User-Agent": "Mozilla/5.0 (escrituras CABA ETL)"}

# El cert de este host está bien: acá NO va verify=False (a diferencia de las fuentes .gob.ar).

# Primer mes que publica la fuente.
INICIO = dt.date(2016, 2, 1)


class FormatoInesperado(RuntimeError):
    """El informe no tiene la forma que espera el parser. No se adivina: se corta."""


MESES = {m: i + 1 for i, m in enumerate(
    "enero febrero marzo abril mayo junio julio agosto septiembre "
    "octubre noviembre diciembre".split())}
MESES["setiembre"] = 9  # variante ortográfica que la fuente usa a veces

# "...realizadas en julio 2026" / "...realizadas en el mes de julio de 2026"
_TITULO = re.compile(r"realizadas?\s+en\s+(?:el\s+mes\s+de\s+)?([a-z]+)\s*(?:de\s*)?(\d{4})")

# "Actos de escrituras de compraventa 6051" y la variante de 2019 sin el "Actos de".
_ACTOS = re.compile(r"(?:actos\s+de\s+)?escrituras?\s+de\s+compraventa\s*:?\s*([\d][\d.]*)")

# Planillas con una fila por mes (las que sirven para rellenar). Excluye a propósito las de
# hipotecas, montos y cotización, que tienen otra forma.
_RODANTE = re.compile(r"(12[-_]meses|cantidad[-_]de[-_]escrituras[-_]por[-_]mes)", re.IGNORECASE)


def _norm(s: str) -> str:
    """Minúsculas sin acentos, para que el matcheo no dependa de cómo se escribió el título."""
    s = unicodedata.normalize("NFKD", s)
    return "".join(c for c in s if not unicodedata.combining(c)).lower()


def _texto(html: str) -> str:
    return BeautifulSoup(html or "", "lxml").get_text(" ", strip=True)


def mes_del_titulo(titulo: str) -> dt.date:
    """'...realizadas en julio 2026' -> date(2026, 7, 1). Corta si no se reconoce."""
    m = _TITULO.search(_norm(titulo))
    if not m or m.group(1) not in MESES:
        raise FormatoInesperado(f"no se pudo leer el mes del título: {titulo!r}")
    return dt.date(int(m.group(2)), MESES[m.group(1)], 1)


def actos_del_texto(texto: str) -> int:
    """Cantidad de actos del cuerpo del informe. Corta si no está."""
    m = _ACTOS.search(_norm(texto))
    if not m:
        raise FormatoInesperado("no se encontró 'escrituras de compraventa <N>' en el informe")
    return int(m.group(1).replace(".", ""))


def get_posts() -> list[dict]:
    """Todos los informes de la categoría: [{date, actos, url, publicado, html}, ...] ascendente.

    Un post que no parsea NO se saltea en silencio: se devuelve con `error` y el caller decide
    (el run lo reporta como falla). Perder un mes sin enterarse es el peor final posible acá.
    """
    crudos: list[dict] = []
    for pagina in range(1, MAX_PAGINAS + 1):
        resp = http.fetch(
            f"{API}?categories={CATEGORIA}&per_page={PER_PAGE}&page={pagina}"
            f"&orderby=date&order=desc",
            headers=HEADERS, timeout=TIMEOUT)
        datos = resp.json()
        if isinstance(datos, dict):        # {"code": "rest_post_invalid_page_number", ...}
            break
        if not datos:
            break
        crudos.extend(datos)
        if len(datos) < PER_PAGE:
            break

    out = []
    for p in crudos:
        titulo = _texto(p.get("title", {}).get("rendered", ""))
        html = p.get("content", {}).get("rendered", "")
        item = {"url": p.get("link"), "publicado": (p.get("date") or "")[:10],
                "titulo": titulo, "html": html, "date": None, "actos": None, "error": None}
        try:
            item["date"] = mes_del_titulo(titulo)
            item["actos"] = actos_del_texto(_texto(html))
        except FormatoInesperado as e:
            item["error"] = str(e)
        out.append(item)
    out.sort(key=lambda x: (x["date"] is None, x["date"]))
    return out


def rodantes_de(html: str) -> list[str]:
    """URLs de las planillas 'una fila por mes' que linkea un informe."""
    soup = BeautifulSoup(html or "", "lxml")
    return [a["href"] for a in soup.find_all("a", href=True)
            if _RODANTE.search(a["href"]) and a["href"].lower().endswith((".xls", ".xlsx"))]


def leer_rodante(url: str) -> dict[dt.date, int]:
    """Planilla rodante -> {mes: cantidad de actos}.

    Sólo se leen las filas cuya primera celda es una **fecha real de Excel**: la fuente escribe
    a veces el mes como texto y con typos (`juk-26`), y adivinar ahí es cómo se carga un dato
    en el mes equivocado. La columna 1 es la cantidad de actos.
    """
    resp = http.fetch(url, headers=HEADERS, timeout=TIMEOUT)
    wb = xlrd.open_workbook(file_contents=resp.content)
    out: dict[dt.date, int] = {}
    for nombre in wb.sheet_names():
        sh = wb.sheet_by_name(nombre)
        for fila in range(sh.nrows):
            celda = sh.cell(fila, 0)
            if celda.ctype != xlrd.XL_CELL_DATE:
                continue
            try:
                y, m, *_ = xlrd.xldate_as_tuple(celda.value, wb.datemode)
            except Exception:
                continue
            valor = sh.cell_value(fila, 1) if sh.ncols > 1 else None
            if isinstance(valor, float) and valor > 0:
                out[dt.date(y, m, 1)] = int(valor)
    return out
