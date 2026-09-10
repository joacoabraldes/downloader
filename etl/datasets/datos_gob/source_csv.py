"""Fuente: CSV de cuadros del INDEC (`indec.gob.ar/ftp/cuadros/...`).

Segunda fuente del dataset, y la PRIMARIA para las series que lista `config.SERIES_CSV`. La API
de series de tiempo queda de respaldo para esas: ver la nota larga en config.py.

Por qué existe: el feed de `apis.datos.gob.ar` va atrasado respecto de lo que el organismo ya
publicó. Medido el 2026-09-10 sobre el índice de salarios, la API cortaba en 2026-04 y este CSV
—linkeado desde el propio informe de prensa— ya traía 2026-05 y 2026-06.

El formato es el de los cuadros del INDEC, no el de una API:

  periodo;IS_sector_privado_registrado;IS_sector_publico;...
  1/10/2015;73,97;75,24;...
  1/4/2026;9292,93;7515,45;...

  - separador `;`, no coma (la coma es el separador DECIMAL)
  - fecha `d/m/aaaa` SIN cero a la izquierda, y el día es siempre 1 (es un dato mensual)
  - `NA` para el tramo en que la serie todavía no existía; NO es un cero ni un error
  - encoding con BOM: hay que abrir con `utf-8-sig` o la primera columna sale como `\\ufeffperiodo`

La URL es FIJA, sin fecha en el nombre, así que no hay que scrapear ningún link para llegar al
archivo del mes. Eso es justamente lo que hace viable usarlo como primaria.
"""
from __future__ import annotations

import csv
import datetime as dt
import io

import requests

HEADERS = {"User-Agent": "Mozilla/5.0 (downloader ETL; cuadros INDEC)"}
TIMEOUT = 60

SESSION = requests.Session()
SESSION.headers.update(HEADERS)

# Mismo cortafuegos que source.py: un cuadro mal armado no debe meter fechas de otro siglo.
ANIO_MIN = 1900

# Mínimo de filas para dar el archivo por bueno. Si la fuente devuelve una página de error con
# 200 (pasa), el CSV parsea a 0-2 filas y el ETL escribiría una serie vacía sin quejarse.
MIN_FILAS = 24


def _anio_max() -> int:
    return dt.date.today().year + 1


def _parse_fecha(texto: str) -> dt.date | None:
    """`1/4/2026` -> date(2026, 4, 1). Sin cero a la izquierda, día siempre 1."""
    partes = texto.strip().split("/")
    if len(partes) != 3:
        return None
    try:
        d, m, y = (int(p) for p in partes)
        return dt.date(y, m, 1)          # el día se normaliza: el dato es mensual
    except ValueError:
        return None


def _parse_valor(texto: str) -> float | None:
    """Coma decimal, SIN separador de miles. `NA` y vacío son huecos legítimos, no errores.

    El punto NO se acepta. Los cuadros del INDEC usan coma decimal y no agrupan miles: los
    valores de cinco cifras vienen `10380,8`, sin un solo punto en todo el archivo (verificado
    sobre indice_salarios.csv el 2026-09-10).

    Antes esto hacía `t.replace(".", "").replace(",", ".")`, tratando el punto como separador de
    miles. Eso no defendía de nada —la fuente no lo usa— y abría una falla silenciosa de un orden
    de magnitud: si el organismo migrara a punto decimal, `9292.93` se leía `929293`. Un valor
    diez o cien veces más grande, sin excepción y sin descarte, en la fuente PRIMARIA de cinco
    series. Ante un formato que no reconocemos hay que fallar, no adivinar.
    """
    t = texto.strip()
    if not t or t.upper() == "NA":
        return None
    if "." in t:
        raise ValueError(
            f"valor {t!r} trae un punto. Los cuadros del INDEC usan coma decimal y no agrupan "
            f"miles, así que un punto significa que la fuente cambió de formato. Se corta la "
            f"corrida en vez de arriesgar un valor mal parseado: revisar el cuadro y actualizar "
            f"`_parse_valor`.")
    try:
        return float(t.replace(",", "."))
    except ValueError:
        return None


def get_cuadro(url: str, columnas: dict[str, str]) -> tuple[dict[str, list], list[str]]:
    """Baja un cuadro y lo parte en una serie por columna.

    `columnas` mapea nombre de columna del CSV -> slug de serie nuestro.

    Devuelve ({serie: [(fecha, valor), ...]}, descartes), con los mismos criterios que
    `source.get_serie`: un descarte es una descripción legible de por qué se tiró un dato.
    """
    resp = SESSION.get(url, timeout=TIMEOUT)
    resp.raise_for_status()

    # utf-8-sig se come el BOM si está y no molesta si no. Los cuadros del INDEC lo traen.
    lector = csv.DictReader(io.StringIO(resp.content.decode("utf-8-sig")), delimiter=";")
    faltantes = [c for c in columnas if c not in (lector.fieldnames or [])]
    if faltantes:
        # La fuente cambió de formato. Fallar es correcto: seguir daría series vacías en
        # silencio, que es peor que no correr.
        raise ValueError(f"columna(s) ausente(s) en {url}: {', '.join(faltantes)}. "
                         f"Encabezado recibido: {lector.fieldnames}")

    series: dict[str, list[tuple[dt.date, float]]] = {s: [] for s in columnas.values()}
    descartes: list[str] = []
    n_filas = 0
    for fila in lector:
        n_filas += 1
        fecha = _parse_fecha(fila.get("periodo") or "")
        if fecha is None:
            descartes.append(f"fecha ilegible {fila.get('periodo')!r}")
            continue
        if not (ANIO_MIN <= fecha.year <= _anio_max()):
            descartes.append(f"fecha fuera de rango {fecha}")
            continue
        for columna, serie in columnas.items():
            valor = _parse_valor(fila.get(columna) or "")
            if valor is not None:            # None = hueco legítimo (`NA`), no se reporta
                series[serie].append((fecha, valor))

    if n_filas < MIN_FILAS:
        raise ValueError(f"{url} devolvió {n_filas} filas (mínimo {MIN_FILAS}). "
                         f"Probable página de error servida con HTTP 200.")

    for filas in series.values():
        filas.sort(key=lambda x: x[0])
    return series, descartes


if __name__ == "__main__":  # smoke test
    from . import config
    for nombre, cuadro in config.SERIES_CSV.items():
        series, desc = get_cuadro(cuadro["url"], cuadro["columnas"])
        print(f"[{nombre}] descartes={len(desc)}")
        for serie, filas in sorted(series.items()):
            rango = f"{filas[0][0]}..{filas[-1][0]}" if filas else "(vacia)"
            print(f"  {serie:36} {len(filas):>4} datapoints  {rango}")
