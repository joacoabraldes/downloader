"""Fuente MAGyP: compras de granos y DJVE (informe semanal), con sus TRES formatos históricos.

La fuente no publica ningún archivo descargable: son páginas PHP con tablas HTML. La navegación
es índice de años -> índice de semanas -> una página por semana. Los links semanales del índice
anual están dentro de `javascript:window.open('01_embarque_YYYY-MM-DD.php', ...)`, así que se
extraen por regex del HTML crudo en lugar de por <a href>.

Se scrapea el índice en vez de generar las fechas: aunque los cortes son todos miércoles, no hay
garantía de que se publique todas las semanas (2005 arranca en marzo, y hay años con semanas
faltantes). Generando fechas, un 404 sería indistinguible de "esa semana no se publicó".

El HTML cambió de formato dos veces. `parse_semana` detecta cuál es y despacha:

  2005 - mediados de 2017   "viejo"     Una sola tabla con dos bloques verticales: SECTOR
                        EXPORTADOR (cultivos como filas) y COMPRAS DE LA INDUSTRIA (menos
                        cultivos, con su propia fecha de corte). Sin DJVE: en su lugar publica
                        "embarque estimado acumulado", que es otro concepto. Hay además un bloque
                        VENTAS (potenciales/efectivas) cuyo ENCABEZADO sobrevive hasta 2015 pero
                        cuyos VALORES terminan en 2007/2008: de ahí en más dice "SIN DATOS".

  fines de 2017 - 2018   "moderno"      Siete solapas (una por cultivo), cada una con
                        Exportador / Industria / Total x cosecha. Aparece la DJVE. Métricas:
                        Semanal, Total Acumulado, A Fijar, Fijado, DJVE Acumulado.

  2019 - hoy   "moderno"                Igual, más "Total Precio Hecho" y "Saldo a Fijar".

El corte entre viejo y moderno cae DENTRO de 2017, así que no se despacha por año sino por lo que
tiene el HTML: si hay solapas (`TabbedPanelsTab`) es moderno, si no es viejo.

Los dos formatos modernos comparten estructura y se parsean con el mismo código: las métricas
salen del encabezado real de cada tabla, no de una lista fija por año.

Detalles de la fuente que el parser tiene que absorber:
  - El bloque Industria a veces trae su PROPIA fecha de corte, anterior a la del encabezado
    (visto "AL 27/05/2026" dentro de la página del 01/07/2026). Se guarda en la columna `corte`;
    sin eso, la industria parece repetir valores sin explicación.
  - Debajo de cada fila hay otra entre paréntesis con el mismo período del año anterior. NO se
    guarda: es redundante (ese dato ya entra por la página de aquel año) y viene sin identificar
    a qué corte exacto corresponde.
  - Dos convenciones numéricas conviviendo: la habitual es la AR ('.' miles, ',' decimales), pero
    2005-2006 usa el punto como decimal ('6500.0'). Se resuelve por forma (ver `_num`).
  - Marcas (*) / (**) pegadas a los números, y notas al pie mezcladas entre las filas de datos.
  - Varias páginas viejas repiten la MISMA tabla dos veces dentro del HTML (ver `_dedup`).
  - Typos en la fecha de corte: la página del 25/12/2019 declara la industria "AL 25/12/10".
  - gov.ar declara ISO-8859-1 pero manda Windows-1252, y sus certs suelen fallar (verify=False).
"""
from __future__ import annotations

import datetime as dt
import re
import time
import unicodedata

import requests
from bs4 import BeautifulSoup

# Códigos que se reintentan: throttling y errores de servidor. El 403 entra acá porque es lo que
# devuelve este sitio cuando corta por volumen, no un problema de permisos. El 404 queda afuera
# a propósito: es una página que no existe.
REINTENTABLES = frozenset({403, 429, 500, 502, 503, 504})
REINTENTOS = 4
ESPERA_BASE = 5.0  # segundos; se duplica en cada intento (5, 10, 20)

BASE = (
    "https://www.magyp.gob.ar/sitio/areas/ss_mercados_agropecuarios/areas/granos/"
    "_archivos/000058_Estad%C3%ADsticas/_compras_historicos"
)
# Landing con el listado de años (solo para referencia/documentación de la fuente).
PAGE_URL = (
    "https://www.magyp.gob.ar/sitio/areas/ss_mercados_agropecuarios/areas/granos/"
    "_archivos/000058_Estad%C3%ADsticas/000020_Compras%20y%20DJVE%20de%20Granos.php"
)

_SEMANA_RE = re.compile(r"01_embarque_(\d{4})-(\d{2})-(\d{2})\.php")
_COSECHA_RE = re.compile(r"^\d{2}\s*/\s*\d{2}$")
# Miles con '.' y decimales con ',' (formato AR, el habitual en la fuente).
_NUM_AR_RE = re.compile(r"^-?\d{1,3}(?:\.\d{3})*(?:,\d+)?$|^-?\d+(?:,\d+)?$")
# Decimales con '.' y sin separador de miles: las páginas de 2005-2006 vienen así ('6500.0').
_NUM_PUNTO_RE = re.compile(r"^-?\d+\.\d{1,2}$")
# "AL 01/07/2026", "AL 09/02/05", "AL 02-03-2005".
_CORTE_RE = re.compile(r"AL\s*(\d{1,2})[/-](\d{1,2})[/-](\d{2,4})", re.I)

# Etiqueta de columna -> métrica. Se matchea por prefijo sobre la etiqueta normalizada, así que
# las claves largas tienen que evaluarse antes que las cortas (ver `_metrica`).
_METRICAS = {
    "SEMANAL": "semanal",
    "SEMANA": "semanal",
    "TOTAL COMPRADO": "total_comprado",
    # 2017-2018 titula "Total Acumulado" lo que 2019+ llama "Total Comprado": en ambos es la
    # nota (1) de la tabla, el acumulado comprado de la campaña. Se unifican en una sola métrica.
    "TOTAL ACUMULADO": "total_comprado",
    "TOTAL PRECIO HECHO": "precio_hecho",
    "PRECIO HECHO": "precio_hecho",
    "TOTAL A FIJAR": "a_fijar",
    "TOTAL FIJADO": "fijado",
    "FIJADO TOTAL": "fijado",
    "SALDO A FIJAR": "saldo_a_fijar",
    "DJVE ACUMULADO": "djve_acum",
    # La misma columna cambia de nombre según el año: "EMBARQUE ESTIMADO ACUMULADO" (2005-2016),
    # "EMB.EST. ACUMUL." (2005-2006) y "EMBARQUE ACUMULADO" (2017 viejo).
    "EMBARQUE ESTIMADO ACUMULADO": "embarque_estimado",
    "EMBARQUE ACUMULADO": "embarque_estimado",
    "EMB.EST. ACUMUL": "embarque_estimado",
    "POTENCIALES": "ventas_potenciales",
    "EFECTIVAS": "ventas_efectivas",
    "COMPRAS ESTIMADAS": "compras_estimadas",
    "COMPRAS DECLARADAS": "compras_declaradas",
}
# Se matchea sobre la etiqueta SIN espacios: la fuente a veces se come los separadores
# ('COMPRASESTIMADAS(*)', 'FIJADOTOTAL(3)' en el bloque industria de 2015). Comparando con
# espacios, esas columnas no matcheaban y sus valores se alineaban contra la métrica siguiente:
# las compras estimadas entraban a la base como 'a_fijar', sin ningún error visible.
_METRICAS_ORD = sorted(((k.replace(" ", ""), v) for k, v in _METRICAS.items()),
                       key=lambda kv: len(kv[0]), reverse=True)

# Nombre de cultivo (solapa moderna o fila del formato viejo) -> slug. Se matchea por prefijo
# con las claves más largas primero (ver `_cultivo`). NO hay entrada para "CEBADA" a secas a
# propósito: el formato viejo escribe "CEBADA CERV." y "CEBADA FORRAJ.", y un fallback genérico
# mandaría la forrajera al bucket de la cervecera sin que nadie se entere.
_CULTIVOS = {
    "TRIGO": "trigo",
    "TRIGO PAN": "trigo",
    "MAIZ": "maiz",
    "SORGO": "sorgo",
    "CEBADA CERVECERA": "cebada_cervecera",
    "CEBADA CERV": "cebada_cervecera",
    "CEBADA FORRAJERA": "cebada_forrajera",
    "CEBADA FORRAJ": "cebada_forrajera",
    "SOJA": "soja",
    "GIRASOL": "girasol",
}

# Antigüedad máxima aceptada para la fecha de corte de un bloque respecto de la fecha de la
# página (ver `_corte`).
_CORTE_MAX_ATRASO = dt.timedelta(days=400)


class FormatoDesconocido(Exception):
    """El HTML no matchea ninguno de los formatos conocidos: hay que revisar el parser."""


class SemanaSinDatos(Exception):
    """La fuente publicó la página pero avisa que esa semana no tiene datos propios.

    Pasa cuando un feriado corre el corte: la página del 26/12/2013 dice "Las Compras, Ventas y
    Embarques del 26/12/2013 se encuentran acumuladas en el archivo posterior con fecha
    03/01/2014". No es una falla del ETL y no debe contarse como tal.
    """


# Aviso explícito de semana acumulada en otra + tope de texto por debajo del cual una página sin
# filas es un aviso y no una tabla que el parser no supo leer (las tablas reales pasan los 3 KB).
_AVISO_RE = re.compile(r"se encuentran acumulad|no se public|sin informaci", re.I)
_AVISO_MAX_TEXTO = 800


# --------------------------------------------------------------------------- URLs y descarga

def url_anio(anio: int) -> str:
    return f"{BASE}/{anio}/{anio}.php"


def url_semana(fecha: dt.date) -> str:
    return f"{BASE}/{fecha.year}/01_embarque_{fecha:%Y-%m-%d}.php"


def fetch_html(url: str, timeout: int = 60, reintentos: int = REINTENTOS) -> str:
    """Baja el HTML, con reintentos y backoff ante throttling.

    Fija el encoding real (gov.ar declara ISO-8859-1 y manda Windows-1252) y desactiva verify
    (sus certs suelen fallar).

    El sitio **corta por volumen de requests**: en el backfill inicial, tras ~700 páginas seguidas
    empezó a devolver 403 en TODO, índices incluidos, y siguió bloqueando un buen rato. Por eso
    el 403 se reintenta con espera creciente en lugar de darse por perdido: un ETL semanal que
    baja 4 páginas no debería fallar por un corte transitorio. El 404 NO se reintenta — es una
    página que no existe, no un bloqueo (ver `load_history`).
    """
    espera = ESPERA_BASE
    for intento in range(reintentos):
        resp = requests.get(url, timeout=timeout, verify=False,
                            headers={"User-Agent": "Mozilla/5.0"})
        if resp.status_code in REINTENTABLES and intento < reintentos - 1:
            time.sleep(espera)
            espera *= 2
            continue
        resp.raise_for_status()
        resp.encoding = resp.apparent_encoding
        return resp.text
    raise RuntimeError(f"inalcanzable: {url}")  # el loop siempre sale por return o raise


def parse_indice_anual(html: str) -> list[dt.date]:
    """Fechas de corte publicadas en el índice de un año, ascendentes y sin repetir.

    Los links van por `window.open(...)`, así que se sacan por regex del HTML crudo. Algunos
    índices linkean semanas de otros años: eso no molesta, el año sale de la fecha del link.
    """
    fechas = set()
    for y, m, d in _SEMANA_RE.findall(html):
        try:
            fechas.add(dt.date(int(y), int(m), int(d)))
        except ValueError:  # fecha inválida en un link roto de la fuente
            continue
    return sorted(fechas)


# --------------------------------------------------------------------------- helpers de texto

def _norm(s: str) -> str:
    """Mayúsculas sin acentos, sin paréntesis y con espacios colapsados."""
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"\([^)]*\)", " ", s)
    return re.sub(r"\s+", " ", s).strip().upper()


def _metrica(label: str) -> str | None:
    norm = _norm(label).replace(" ", "")
    if not norm:
        return None
    for clave, metrica in _METRICAS_ORD:
        if norm.startswith(clave):
            return metrica
    return None


def _cultivo(label: str) -> str | None:
    # Sin espacios, por el mismo motivo que en `_metrica`: la fuente escribe indistintamente
    # 'CEBADA FORRAJ.(**)' y 'CEBADAFORRAJ.(**)'.
    norm = _norm(label).replace(" ", "")
    for clave, cultivo in sorted(((k.replace(" ", ""), v) for k, v in _CULTIVOS.items()),
                                 key=lambda kv: len(kv[0]), reverse=True):
        if norm.startswith(clave):
            return cultivo
    return None


def _sector(label: str) -> str | None:
    norm = _norm(label)
    if "EXPORTADOR" in norm:
        return "exportador"
    if "INDUSTRIA" in norm:
        return "industria"
    if norm.startswith("TOTAL"):
        return "total"
    return None


def _num(s: str) -> float | None:
    """Número de la fuente, tolerando marcas (*) pegadas. Convive con DOS convenciones.

    Lo normal es el formato AR ('.' miles, ',' decimales: '1.971,7'). Pero las páginas de
    2005-2006 usan el punto como decimal y sin separador de miles ('6500.0', '11280.0'). Se
    resuelve por forma, no por año: con coma presente manda la convención AR; sin coma, un
    punto seguido de 1-2 dígitos sólo puede ser decimal, y los grupos de 3 sólo pueden ser
    miles. Aplicar la regla AR a secas descartaba TODOS los valores de 2005-2006.
    """
    s = re.sub(r"\(\*+\)", "", s).strip()
    if "," not in s and _NUM_PUNTO_RE.match(s):
        return float(s)
    if not _NUM_AR_RE.match(s):
        return None
    try:
        return float(s.replace(".", "").replace(",", "."))
    except ValueError:
        return None


def _corte(texto: str, default: dt.date | None = None,
           pagina: dt.date | None = None) -> dt.date | None:
    """Fecha de corte de un encabezado ('... AL 27/05/2026'). Años de 2 dígitos -> 20xx.

    Con `pagina`, descarta la fecha si no es plausible (posterior a la página, o más de
    `_CORTE_MAX_ATRASO` anterior) y devuelve `default`. La fuente tiene typos en este campo: en
    la página del 25/12/2019, soja y girasol dicen "Compras de la Industria (AL 25/12/10)" —
    nueve años antes. Sin este filtro, ese typo entra a la base como fecha de corte real.
    """
    m = _CORTE_RE.search(texto)
    if not m:
        return default
    d, mth, y = (int(x) for x in m.groups())
    if y < 100:
        y += 2000
    try:
        corte = dt.date(y, mth, d)
    except ValueError:
        return default
    if pagina is not None and not (pagina - _CORTE_MAX_ATRASO <= corte <= pagina):
        return default
    return corte


def _filas(tabla) -> list[list[str]]:
    """Filas de una tabla como listas de celdas no vacías, con espacios colapsados."""
    out = []
    for tr in tabla.find_all("tr"):
        celdas = [re.sub(r"\s+", " ", c.get_text(" ", strip=True)).strip()
                  for c in tr.find_all(["td", "th"])]
        celdas = [c for c in celdas if c]
        if celdas:
            out.append(celdas)
    return out


def _es_anio_anterior(celda: str) -> bool:
    """Fila de comparación con el año anterior: arranca con un valor entre paréntesis."""
    return celda.startswith("(")


# --------------------------------------------------------------------------- formato moderno

def _parse_moderno(soup: BeautifulSoup, fecha: dt.date) -> list[dict]:
    """Formato moderno (fines de 2017 a hoy): una solapa por cultivo, filas por sector y cosecha.

    El cultivo sale del par (TabbedPanelsTab[i], TabbedPanelsContent[i]): es la única marca que
    existe en TODAS las variantes modernas. Los comentarios `<!-- TabbedPanelsContent trigo -->`
    aparecen recién en 2026, y el nombre del cultivo no está dentro de la tabla.
    """
    tabs = soup.find_all(class_="TabbedPanelsTab")
    panels = soup.find_all(class_="TabbedPanelsContent")
    if not tabs or len(tabs) != len(panels):
        raise FormatoDesconocido(
            f"solapas inconsistentes: {len(tabs)} tabs vs {len(panels)} paneles")

    filas: list[dict] = []
    for tab, panel in zip(tabs, panels):
        cultivo = _cultivo(tab.get_text(" ", strip=True))
        tabla = panel.find("table")
        if cultivo is None or tabla is None:
            continue
        filas.extend(_parse_tabla_moderna(_filas(tabla), cultivo, fecha))
    if not filas:
        raise FormatoDesconocido("formato moderno sin ninguna fila parseada")
    return filas


def _parse_tabla_moderna(filas: list[list[str]], cultivo: str, fecha: dt.date) -> list[dict]:
    if not filas:
        return []
    # Encabezado: [<titulo con la fecha de corte>, 'Cosecha', <metrica>, <metrica>, ...]
    head = filas[0]
    try:
        i_cos = next(i for i, c in enumerate(head) if _norm(c) == "COSECHA")
    except StopIteration:
        raise FormatoDesconocido(f"{cultivo}: encabezado sin columna Cosecha: {head[:4]}")
    metricas = [_metrica(c) for c in head[i_cos + 1:]]
    corte_tabla = _corte(head[0], fecha, fecha)

    out: list[dict] = []
    sector = corte = None
    for row in filas[1:]:
        if _es_anio_anterior(row[0]):
            continue  # fila de comparación con el año anterior (redundante, ver docstring)
        if _COSECHA_RE.match(row[0]):
            cosecha, nums = row[0], row[1:]  # continuación del sector anterior
        else:
            sector = _sector(row[0])
            corte = _corte(row[0], corte_tabla, fecha)
            if sector is None or len(row) < 2 or not _COSECHA_RE.match(row[1]):
                continue  # fila de notas al pie
            cosecha, nums = row[1], row[2:]
        if sector is None:
            continue
        out.extend(_emitir(cultivo, cosecha, sector, metricas, nums, fecha, corte))
    return out


# --------------------------------------------------------------------------- formato viejo

def _parse_viejo(soup: BeautifulSoup, fecha: dt.date) -> list[dict]:
    """Formato viejo (2005 a mediados de 2017): dos bloques verticales en la misma tabla.

    Los bloques no están en tablas separadas ni marcados por clase: se detectan por sus títulos
    y sus encabezados de columna, recorriendo las filas en orden.
    """
    filas: list[list[str]] = []
    for tabla in soup.find_all("table"):
        filas.extend(_filas(tabla))

    out: list[dict] = []
    sector = None
    metricas: list[str | None] = []
    base: list[str] = []          # métricas acumuladas de las filas de encabezado del bloque
    tiene_embarque = False
    cultivo = None
    corte = fecha

    for row in filas:
        if _es_anio_anterior(row[0]):
            continue  # fila de comparación con la cosecha anterior
        texto = _norm(" ".join(row))

        # Títulos de bloque. Se buscan en la fila ENTERA y no sólo en su primera celda: en 2017
        # el título viene como tercera celda del propio encabezado de columnas
        # ('PRODUCTO | COSECHA | COMPRAS Y EMBARQUES DEL SECTOR EXPORTADOR AL ... | EMBARQUE').
        # Por eso tampoco se corta acá con un `continue`: la misma fila puede traer etiquetas.
        titulo_exp = "EXPORTADOR" in texto and "COMPRAS" in texto
        titulo_ind = "COMPRAS DE LA INDUSTRIA" in texto
        if titulo_exp or titulo_ind:
            sector = "exportador" if titulo_exp else "industria"
            cultivo, metricas, base, tiene_embarque = None, [], [], False
            corte = _corte(texto, fecha, fecha)
        if sector is None:
            continue

        # El corte del bloque puede venir SOLO en su fila o como primera celda del encabezado de
        # columnas ('AL 29/05/13 | COMPRAS ESTIMADAS | ...'). Se extrae y se sigue evaluando la
        # fila: cortar acá con un `continue` perdía el encabezado entero del bloque industria.
        if _CORTE_RE.search(row[0]):
            corte = _corte(row[0], corte, fecha)

        # Encabezados: el del exportador viene en DOS filas (grupo + subcolumnas), así que las
        # etiquetas se acumulan entre filas. Las no mapeables ('PRODUCTO', 'VENTAS' cuando no
        # tiene subcolumnas, 'SIN DATOS') se descartan.
        mapeadas = [_metrica(c) for c in row]
        if any(mapeadas) and not any(_num(c) is not None for c in row):
            # 'embarque_estimado' se anuncia en la fila de grupo pero es la ÚLTIMA columna de
            # datos, después de las subcolumnas: se lo recuerda aparte y se lo agrega al final.
            tiene_embarque = tiene_embarque or "embarque_estimado" in mapeadas
            base += [m for m in mapeadas if m and m != "embarque_estimado"]
            metricas = base + (["embarque_estimado"] if tiene_embarque else [])
            continue

        if not metricas:
            continue

        # Filas de datos: [<producto>, <cosecha>, nums...] o [<cosecha>, nums...].
        if _COSECHA_RE.match(row[0]):
            cosecha, nums = row[0], row[1:]
        else:
            nuevo = _cultivo(row[0])
            if nuevo is None or len(row) < 2 or not _COSECHA_RE.match(row[1]):
                continue  # notas al pie
            cultivo, cosecha, nums = nuevo, row[1], row[2:]
        if cultivo is None:
            continue
        out.extend(_emitir(cultivo, cosecha, sector, metricas, nums, fecha, corte))

    if not out:
        raise FormatoDesconocido("formato viejo sin ninguna fila parseada")
    return out


# --------------------------------------------------------------------------- emisión de filas

def _emitir(cultivo: str, cosecha: str, sector: str, metricas: list[str | None],
            nums: list[str], fecha: dt.date, corte: dt.date | None) -> list[dict]:
    """Mapea los valores de una fila contra las métricas, alineando por la izquierda.

    La fuente omite columnas del final cuando no tiene el dato (p.ej. soja sin embarque estimado
    en 2005), nunca del medio, así que las métricas sobrantes simplemente no se emiten.
    """
    out = []
    for metrica, celda in zip(metricas, nums):
        if metrica is None:
            continue
        valor = _num(celda)
        if valor is None:
            continue
        out.append({
            "cultivo": cultivo,
            "cosecha": cosecha.replace(" ", ""),
            "sector": sector,
            "metrica": metrica,
            "date": fecha,
            "valor": valor,
            "corte": corte,
        })
    return out


def _dedup(filas: list[dict]) -> list[dict]:
    """Una fila por (cultivo, cosecha, sector, metrica); gana la primera aparición.

    Varias páginas del formato viejo traen la MISMA tabla repetida dos veces dentro del HTML
    (visto en 2005 y 2010). Sin este filtro, el camino con dedup de la base lo absorbe pero el
    backfill masivo —que inserta sin deduplicar— mete cada valor dos veces: la tabla queda al
    doble y aparenta dos snapshots donde la fuente publicó uno solo. Los duplicados son idénticos,
    así que quedarse con el primero no pierde nada.
    """
    vistas: dict[tuple, dict] = {}
    for f in filas:
        vistas.setdefault((f["cultivo"], f["cosecha"], f["sector"], f["metrica"]), f)
    return list(vistas.values())


def parse_semana(html: str, fecha: dt.date) -> list[dict]:
    """Filas de una página semanal. Despacha por formato.

    Cada fila: {cultivo, cosecha, sector, metrica, date, valor, corte}.

    Lanza `SemanaSinDatos` si la página es un aviso (semana acumulada en otra) y
    `FormatoDesconocido` si tiene tabla pero el parser no pudo leerla.
    """
    soup = BeautifulSoup(html, "lxml")
    try:
        if soup.find_all(class_="TabbedPanelsTab"):
            return _dedup(_parse_moderno(soup, fecha))
        return _dedup(_parse_viejo(soup, fecha))
    except FormatoDesconocido:
        texto = re.sub(r"\s+", " ", soup.get_text(" ", strip=True))
        if _AVISO_RE.search(texto) or len(texto) < _AVISO_MAX_TEXTO:
            raise SemanaSinDatos(texto[:200]) from None
        raise


def get_semana(fecha: dt.date) -> tuple[list[dict], str]:
    """Baja y parsea una semana. Devuelve (filas, url)."""
    url = url_semana(fecha)
    return parse_semana(fetch_html(url), fecha), url


if __name__ == "__main__":  # smoke test manual
    import urllib3
    urllib3.disable_warnings()
    for f in (dt.date(2005, 3, 2), dt.date(2014, 6, 4), dt.date(2017, 12, 27),
              dt.date(2026, 7, 1)):
        try:
            filas, _ = get_semana(f)
        except Exception as e:  # noqa: BLE001 - smoke test
            print(f"{f}: ERROR {e}")
            continue
        cultivos = sorted({r["cultivo"] for r in filas})
        metricas = sorted({r["metrica"] for r in filas})
        print(f"{f}: filas={len(filas)} cultivos={cultivos} metricas={metricas}")
