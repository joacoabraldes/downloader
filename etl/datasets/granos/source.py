"""Fuentes MAGyP: builders de URL + parser del HTML de molienda de oleaginosas.

La página trae las 4 secciones (GRANOS OLEAGINOSOS, ACEITES, PELLETS, EXPELLERS),
cada una con 7 granos: soja, girasol, lino, maní, algodón, cártamo, canola.
Nos quedamos SOLO con GRANOS OLEAGINOSOS (la molienda / crush), en toneladas.

Estrategia de parsing robusta (la tabla HTML está anidada y duplicada):
  - aplanar el HTML a texto con BeautifulSoup .get_text("\\n") y trabajar por líneas,
  - trackear la sección actual por los headers ("G R A N O S O L E A G I N O S O S"
    activa modo granos; "PELLETS"/"EXPELLERS" lo desactivan),
  - trackear el año por líneas tipo ^(19|20)\\d\\d$,
  - ante un nombre de mes dentro de la sección granos, leer las 14 cifras que siguen
    (7 granos + 7 aceites) y quedarse con las primeras 7,
  - acumular en un dict keyeado por date: las tablas repetidas traen valores idénticos,
    así que sobrescribir es idempotente.
"""
from __future__ import annotations

import datetime as dt
import re

from bs4 import BeautifulSoup

from etl.core import http

# URL de la página de provisorios.
PAGE_URL = (
    "https://www.magyp.gob.ar/sitio/areas/ss_mercados_agropecuarios/areas/granos/"
    "_archivos/000058_Estad%C3%ADsticas/"
    "000032_Evolucion%20de%20la%20Molienda%20(Cereales%20y%20Oleaginosas)/"
    "000002_Evoluci%C3%B3n%20de%20la%20Molienda%20Mensual%20-%20Oleaginosas/"
    "000002_Evoluci%C3%B3n%20de%20la%20Molienda%20Mensual%20-%20Oleaginosas.php"
)

# El Excel histórico cuelga del mismo directorio con un link relativo.
EXCEL_URL = PAGE_URL.rsplit("/", 1)[0] + "/descarga_molienda_oleaginosas_historico.xlsx"

# Orden de los 7 granos tal como aparecen en cada fila de la sección.
GRANOS = ["soja", "girasol", "lino", "mani", "algodon", "cartamo", "canola"]
NUMS_PER_MONTH = 14  # 7 granos + 7 aceites en la tabla GRANOS/ACEITES

MESES = {
    "ENERO": 1, "FEBRERO": 2, "MARZO": 3, "ABRIL": 4, "MAYO": 5, "JUNIO": 6,
    "JULIO": 7, "AGOSTO": 8, "SEPTIEMBRE": 9, "OCTUBRE": 10, "NOVIEMBRE": 11,
    "DICIEMBRE": 12,
}

_YEAR_RE = re.compile(r"^(?:19|20)\d\d$")
_NUM_RE = re.compile(r"^-?[\d.]+$")


def fetch_html(url: str = PAGE_URL, timeout: int = 60) -> str:
    """Baja el HTML con reintentos y backoff (ver `etl.core.http`: MAGyP corta por volumen y
    también deja morir la conexión por timeout). Fija el encoding real: gov.ar declara
    ISO-8859-1 pero suele mandar Windows-1252."""
    resp = http.fetch(url, timeout=timeout, verify=False)
    resp.encoding = resp.apparent_encoding
    return resp.text


def _despace(s: str) -> str:
    return re.sub(r"\s+", "", s).upper()


def _to_int(s: str) -> int | None:
    """Número AR: el '.' es separador de miles."""
    if not _NUM_RE.match(s):
        return None
    s = s.replace(".", "")
    try:
        return int(s)
    except ValueError:
        return None


def parse_molienda(html: str) -> dict[dt.date, dict]:
    """Devuelve {date(primer día del mes): {soja,...,canola, valor(total)}}.

    Solo procesa la sección GRANOS OLEAGINOSOS. Tolera HTML anidado/duplicado.
    """
    soup = BeautifulSoup(html, "lxml")
    lines = [ln.strip() for ln in soup.get_text("\n").split("\n") if ln.strip()]

    out: dict[dt.date, dict] = {}
    section = "other"      # 'granos' mientras estemos bajo el header de granos
    year: int | None = None
    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        flat = _despace(line)

        if flat.startswith("GRANOSOLEAGINOSOS"):
            section = "granos"
            i += 1
            continue
        if flat.startswith("PELLETS") or flat.startswith("EXPELLERS"):
            section = "other"
            i += 1
            continue

        if section != "granos":
            i += 1
            continue

        if _YEAR_RE.match(line):
            year = int(line)
            i += 1
            continue

        month = MESES.get(flat)
        is_total = flat.startswith("TOTAL")
        if month or is_total:
            # consumir hasta 14 cifras consecutivas que siguen
            nums: list[int] = []
            j = i + 1
            while j < n and len(nums) < NUMS_PER_MONTH:
                v = _to_int(lines[j])
                if v is None:
                    break
                nums.append(v)
                j += 1
            if month and year and len(nums) >= len(GRANOS):
                granos = nums[: len(GRANOS)]
                date = dt.date(year, month, 1)
                row = {g: float(v) for g, v in zip(GRANOS, granos)}
                row["valor"] = float(sum(granos))
                out[date] = row  # sobrescribe duplicados con valores idénticos
            i = j
            continue

        i += 1

    return out


def get_month(html: str, date: dt.date) -> dict | None:
    """Devuelve la fila de un mes puntual, o None si no está publicado."""
    return parse_molienda(html).get(dt.date(date.year, date.month, 1))


if __name__ == "__main__":  # smoke test manual
    import urllib3
    urllib3.disable_warnings()
    data = parse_molienda(fetch_html())
    print(f"meses parseados: {len(data)}  rango: {min(data)} .. {max(data)}")
    for d in sorted(data)[-4:]:
        print(d, data[d])
