"""Fuente CAA: descubre el último PDF de "Cifras" y parsea la serie de acero crudo.

Los datos están sólo en PDFs (la página no tiene tablas). Hay dos familias: los
`Cifras-<mes>-<año>.pdf` (la tabla de producción, la que sirve) y los `CAA-INFORME-*`
(prosa). El nombre del PDF de cifras es inconsistente (`cifrasenero2026-sin-grafico.pdf`,
`Cifras-mayo-2026.pdf`, ...), así que no se puede construir la URL: hay que descubrir los
archivos y quedarse con el más nuevo (mayor año/mes parseado del nombre).

Cada PDF trae los últimos ~13 meses. El texto extrae limpio y en orden: cada fila mensual
es `<Mes> <Año>` seguido de 8 valores (arrabio, esponja, total hierro primario, ACERO CRUDO,
planos 1, planos 2, total laminados, planos en frío). Tomamos la 4ª (acero crudo).

El descubrimiento SOLO por link tenía un punto ciego: la CAA sube el PDF a
`wp-content/uploads/<año>/<mes de subida>/` y a veces tarda días en linkearlo en la página.
Mientras tanto el archivo ya se baja por ruta directa, pero el scrapeo de links no lo ve.
Pasó dos veces: julio 2026 (subido el 2026-08-17, cargado a mano el 2026-08-25 con la página
todavía sin el link) y agosto 2026 (subido el 2026-09-17 20:05, seis días sin linkear; la
corrida del 2026-09-23 leyó julio y cerró con trece `sin_cambios`, sin avisar nada).

No se puede construir la URL para adelantarse: el nombre tuvo 4 convenciones distintas en 6
archivos, y la carpeta es el mes de subida, no el del dato. Lo que SÍ se puede es preguntarle
a WordPress. acero.org.ar corre WordPress y expone `wp-json/wp/v2/media`, que lista la
biblioteca de medios entera —linkeada o no— ordenada por fecha de subida. Un request, JSON.
Por eso el descubrimiento ahora mira las dos fuentes y se queda con el mes más nuevo:

  - media API (`_from_media`): primaria de hecho, porque para linkear hay que haber subido.
  - scrapeo de la página (`_from_page`): respaldo si la API se cae o la desactivan.

Ninguna de las dos es obligatoria: si UNA falla se sigue con la otra y queda una nota en el
reporte. Si fallan las DOS la corrida falla, que es lo correcto. Ese aviso no es decorativo —
sin él, perder la media API devolvería el ETL al punto ciego de antes en silencio.
"""
from __future__ import annotations

import datetime as dt
import io
import re
import urllib.parse

from bs4 import BeautifulSoup

from etl.core import http
from . import config

COMUNICADOS_URL = "https://www.acero.org.ar/comunicados-cifras-2023-2-2-2/"
# Biblioteca de medios de WordPress: lista los PDFs subidos aunque la página no los linkee.
MEDIA_URL = "https://www.acero.org.ar/wp-json/wp/v2/media"
MEDIA_QUERY = {"search": "cifras", "per_page": 100, "orderby": "date", "order": "desc"}
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


def _parse_cifras_name(url: str) -> tuple[int, int] | None:
    """(año, mes) del nombre del PDF de Cifras, o None si el archivo no es uno.

    Filtra por nombre y no por ruta porque las dos fuentes de descubrimiento entregan URLs
    distintas para el mismo archivo. De paso descarta la otra familia de PDFs de la CAA:
    `Cifras-Mensuales_08_2026_v2.pdf` no trae el mes en palabras, así que `_NAME_RX` no
    matchea y queda afuera, que es lo que queremos (ese PDF es prosa, no la tabla).
    """
    name = url.rsplit("/", 1)[-1].lower()
    if "cifras" not in name or not name.endswith(".pdf"):
        return None
    m = _NAME_RX.search(name)
    if not m:
        return None
    return int(m.group(2)), _MESES[m.group(1).lower()]


def _from_page() -> tuple[str, int, int] | None:
    """(url, año, mes) del PDF de Cifras más nuevo LINKEADO en la página de comunicados."""
    r = http.fetch(COMUNICADOS_URL, headers=HEADERS, timeout=TIMEOUT, verify=False)
    soup = BeautifulSoup(r.content, "lxml")
    best = None
    for a in soup.find_all("a", href=True):
        ym = _parse_cifras_name(a["href"])
        if ym and (best is None or ym > (best[1], best[2])):
            best = (a["href"], ym[0], ym[1])
    return best


def _from_media() -> tuple[str, int, int] | None:
    """(url, año, mes) del PDF de Cifras más nuevo SUBIDO, linkeado o no.

    La respuesta viene ordenada por fecha de subida descendente, así que el primer archivo
    de cada mes es la subida más reciente de ese mes. La comparación es `>` estricto para
    conservarlo: así gana la corrección sobre el original cuando la CAA sube dos PDFs del
    mismo mes (`Cifras-diciembre2025-nueva.pdf` el 2026-01-26 contra `Cifrasdiciembre2025.pdf`
    el 2026-01-17).
    """
    url = f"{MEDIA_URL}?{urllib.parse.urlencode(MEDIA_QUERY)}"
    r = http.fetch(url, headers=HEADERS, timeout=TIMEOUT, verify=False)
    best = None
    for item in r.json():
        src = item.get("source_url") or ""
        ym = _parse_cifras_name(src)
        if ym and (best is None or ym > (best[1], best[2])):
            best = (src, ym[0], ym[1])
    return best


def _intentar(fn, que: str, consecuencia: str,
              notas: list[str] | None) -> tuple[str, int, int] | None:
    """Corre un descubridor y devuelve None si no encontró nada, dejando nota del por qué.

    Las dos vías se protegen POR IGUAL y a propósito: cualquiera de las dos puede caerse sin
    voltear la corrida, porque la otra alcanza para encontrar el PDF. Proteger sólo una sería
    peor que no proteger ninguna — dejaría la corrida a merced de la vía sin red justo cuando
    la otra está sana y tiene la respuesta.

    El caso "devolvió None sin levantar excepción" también deja nota. Es una falla MUDA y por
    eso la más peligrosa: la página siempre linkea algún Cifras y la media API siempre lista
    alguno, así que cero resultados no es un estado normal, es un cambio en la fuente
    (markup nuevo, `search` que dejó de matchear) que hay que ver.
    """
    try:
        res = fn()
    except Exception as e:
        if notas is not None:
            notas.append(f"OJO: {que} falló ({type(e).__name__}: {e}). {consecuencia}")
        return None
    if res is None and notas is not None:
        notas.append(f"OJO: {que} no devolvió ningún PDF de Cifras, y eso no es un estado "
                     f"normal. {consecuencia}")
    return res


def find_latest_cifras_pdf(notas: list[str] | None = None) -> tuple[str, int, int]:
    """(url, año, mes) del PDF de Cifras más nuevo, linkeado o no.

    Consulta las dos vías y se queda con el mes más nuevo. Ante empate gana el linkeado: es
    el archivo que la CAA dio por publicado. `notas` recolecta avisos para el reporte.

    Si UNA de las dos falla se sigue con la otra y queda la nota. Si fallan las DOS se levanta
    RuntimeError con las notas adentro: ahí la fuente está realmente caída y la corrida tiene
    que fallar y mandar el mail. Devolver None acá perdía el diagnóstico, porque `run.py`
    cortaba antes de llegar a imprimir las notas.
    """
    linkeado = _intentar(_from_page, "el scrapeo de la página de comunicados",
                         "Se sigue con la media API, que ve todo lo subido.", notas)
    subido = _intentar(_from_media, "la media API de WordPress",
                       "Se sigue sólo con el link de la página; mientras tanto un PDF "
                       "subido y sin linkear no se ve.", notas)
    if linkeado is None and subido is None:
        detalle = " | ".join(notas) if notas else "sin detalle"
        raise RuntimeError(f"no se pudo descubrir el PDF de Cifras por ninguna de las dos "
                           f"vías: {detalle}")
    if subido and (linkeado is None or (subido[1], subido[2]) > (linkeado[1], linkeado[2])):
        if linkeado is not None and notas is not None:
            notas.append(f"PDF subido sin linkear: la página linkea "
                         f"{linkeado[1]}-{linkeado[2]:02d} y la media API tiene "
                         f"{subido[1]}-{subido[2]:02d}. Se usa el subido.")
        return subido
    return linkeado


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


def get_latest() -> tuple[dict[dt.date, float], str, list[str]]:
    """({date: acero_crudo}, url, notas) del último PDF de Cifras.

    Levanta RuntimeError si no se pudo descubrir ningún PDF (ver `find_latest_cifras_pdf`).
    """
    notas: list[str] = []
    url, _, _ = find_latest_cifras_pdf(notas)
    return parse_cifras(download_pdf(url)), url, notas


if __name__ == "__main__":  # smoke test
    import urllib3
    urllib3.disable_warnings()
    res = get_latest()
    if res:
        data, url, notas = res
        for n in notas:
            print(n)
        print(url)
        for d in sorted(data):
            print(f"  {d:%Y-%m}  {data[d]}")
