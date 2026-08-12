"""Fuente: API oficial de Series de Tiempo del Estado (`apis.datos.gob.ar/series`).

Pública, sin API key y con política de no romper compatibilidad desde 2017. La evaluación
completa de esta fuente —incluido por qué NO reemplaza a ninguno de nuestros scrapers— está en
`docs/datos_gob_ar.md`.

Tres cosas que la API hace y hay que contemplar:

1. **Devuelve 403 con el User-Agent por defecto** de `requests`/`urllib`. Hay que mandar uno.
2. **Pagina de a 1000 datapoints.** `count` viene en la respuesta y dice cuántos hay en total,
   así que se pagina con `start` hasta juntarlos.
3. **Sirve fechas basura.** La serie `359.2_ACERO_CRUDUDO__11` devuelve un datapoint fechado en
   **2126-04**. No es un error de metadatos: la API entrega el valor. Por eso se descarta todo
   lo que caiga fuera de un rango razonable en vez de confiar en la fuente.

Se pide **una serie por request**. La API admite varias (`ids=a,b,c`) y sería más rápido, pero
un solo ID roto haría fallar el lote entero; con 9 series el ahorro no compensa perder el
aislamiento de errores.
"""
from __future__ import annotations

import datetime as dt

import requests

BASE = "https://apis.datos.gob.ar/series/api"
# Sin User-Agent explícito la API responde 403.
HEADERS = {"User-Agent": "Mozilla/5.0 (downloader ETL; series de tiempo)"}
TIMEOUT = 60
PAGINA = 1000          # tope de datapoints por request que admite la API
MAX_PAGINAS = 20       # cortafuegos: 20k datapoints es mucho más que cualquier serie mensual

SESSION = requests.Session()
SESSION.headers.update(HEADERS)

# Rango plausible para una serie mensual argentina. Todo lo de afuera es basura de la fuente.
ANIO_MIN = 1900


def _anio_max() -> int:
    """Un año para adelante del actual: tolera un dato adelantado, no un siglo."""
    return dt.date.today().year + 1


def _parse_fecha(texto: str) -> dt.date | None:
    try:
        return dt.date.fromisoformat(texto[:10]).replace(day=1)
    except (ValueError, TypeError):
        return None


def get_serie(serie_id: str) -> tuple[list[tuple[dt.date, float]], list[str]]:
    """Baja una serie completa paginando.

    Devuelve (datapoints ordenados, descartes). Cada descarte es una descripción legible de
    por qué se tiró el dato, para que el reporte lo muestre en vez de tragárselo.
    """
    filas: list[tuple[dt.date, float]] = []
    descartes: list[str] = []
    inicio, total, paginas = 0, None, 0
    while paginas < MAX_PAGINAS:
        resp = SESSION.get(f"{BASE}/series/", timeout=TIMEOUT, params={
            "ids": serie_id, "format": "json", "limit": PAGINA, "start": inicio,
        })
        resp.raise_for_status()
        cuerpo = resp.json()
        datos = cuerpo.get("data") or []
        if total is None:
            total = cuerpo.get("count") or len(datos)
        for fila in datos:
            if not fila:
                continue
            fecha, valor = _parse_fecha(fila[0]), fila[1] if len(fila) > 1 else None
            if valor is None:
                continue                      # hueco de la serie, no es un error
            if fecha is None:
                descartes.append(f"fecha ilegible {fila[0]!r}")
                continue
            if not (ANIO_MIN <= fecha.year <= _anio_max()):
                descartes.append(f"fecha fuera de rango {fecha}")
                continue
            filas.append((fecha, float(valor)))
        inicio += PAGINA
        paginas += 1
        if not datos or inicio >= total:
            break
    filas.sort(key=lambda x: x[0])
    return filas, descartes


def url_serie(serie_id: str) -> str:
    """URL legible de la serie, para guardar en la columna `fuente`."""
    return f"{BASE}/series/?ids={serie_id}&format=json"


if __name__ == "__main__":  # smoke test
    from . import config
    for nombre, meta in config.SERIES_META.items():
        filas, desc = get_serie(meta[0])
        rango = f"{filas[0][0]}..{filas[-1][0]}" if filas else "(vacia)"
        print(f"{nombre:24} {len(filas):>4} datapoints  {rango}  descartes={len(desc)}")
