"""Fuente: planillas .xls de cuadros del INDEC (`indec.gob.ar/ftp/cuadros/...`).

Tercera fuente del dataset, y la PRIMARIA para las series que lista `config.SERIES_XLS`: hoy
`expo_total`, `impo_total` y `saldo_total`, del cuadro de balanza comercial mensual del ICA
(`balanmensual.xls`). La API de series de tiempo queda de respaldo; ver la nota en config.py.

El formato es un cuadro para leer, no una tabla:

  fila  col0        col1         col2           ...  col7           ...  col11
  2     Período                  Exportaciones       Importaciones       Saldo
  3                              Total mensual       Total mensual
  4                              Millones de dólares ...
  6     1990        Enero        795.559099          385.75274           409.806359
  7                 Febrero      811.273028          ...
  ...
  474   2026*       Enero        ...
  481               Agostoe      8882.58249877       ...
  484   Nota: ...
  486   *  Dato provisorio para los años 2024 y 2025
  487   e   Dato estimado para exportaciones de agosto de 2026

  - el año va SÓLO en el primer mes de cada año; el resto hereda el último visto
  - entre años hay una fila vacía
  - las MARCAS van pegadas al texto, sin separador: `2026*` en el año (todo el año provisorio) y
    `Agostoe` en el mes (dato estimado). La leyenda del pie dice "2024 y 2025" pero la celda de
    2026 también trae `*`: manda la celda, no la leyenda.
  - el pie empieza con 'Nota:', '*', 'e', 'Fuente:' en col0 y col1 vacía

Los valores son numéricos (celda float), sin formateo de miles ni coma decimal: acá no hay nada
que parsear, a diferencia del CSV.

TRAMPA DEL INDEC: una URL inexistente bajo indec.gob.ar NO devuelve 404, devuelve una página
HTML con HTTP 200. Por eso se valida la firma del archivo (OLE2, la de un .xls binario), los
encabezados de las columnas que se leen y una cantidad mínima de meses. Sin eso, un cambio de
nombre del archivo pasaría como "planilla sin datos" y la serie quedaría congelada en silencio.
"""
from __future__ import annotations

import datetime as dt
import re

import xlrd

from etl.core import http, meses

TIMEOUT = 60

# Firma de un archivo OLE2 (el .xls binario de Excel 97-2003). Lo primero que se mira: si la
# fuente devolvió HTML con 200, falla acá con un mensaje claro y no adentro de xlrd.
FIRMA_OLE2 = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"

# Mínimo de meses para dar la planilla por buena. La de balanza comercial trae ~440 desde 1990;
# 30 años de piso deja pasar cualquier recorte razonable y frena una planilla truncada.
MIN_MESES = 360

# Cuántas filas del principio se barren buscando el encabezado. Hoy está en la fila 2.
FILAS_ENCABEZADO = 10

# Marcas que el INDEC pega al año o al mes. Cualquier OTRA marca hace fallar la corrida: no
# sabemos qué significa y no se adivina la calidad de un dato.
#   '*'  dato provisorio
#   'e'  dato estimado
MARCAS = {"*": "provisorio", "e": "estimado"}

_RX_ANIO = re.compile(r"(\d{4})\s*([^\d]*)")


def _anio_max() -> int:
    return dt.date.today().year + 1


def _texto(celda) -> str:
    """Texto de una celda. Un año guardado como número (2026.0) sale '2026', no '2026.0'."""
    v = celda.value
    if celda.ctype == xlrd.XL_CELL_NUMBER and float(v).is_integer():
        return str(int(v))
    return str(v).strip()


def _marcas(sufijo: str, donde: str) -> set[str]:
    """Marcas de un sufijo ('*', 'e', ' *'...). Levanta ante un carácter desconocido."""
    sufijo = sufijo.replace(" ", "")
    desconocidas = set(sufijo) - set(MARCAS)
    if desconocidas:
        raise ValueError(f"marca desconocida {''.join(sorted(desconocidas))!r} en {donde}. "
                         f"Conocidas: {MARCAS}. Revisar la leyenda del pie de la planilla.")
    return set(sufijo)


def _parse_anio(texto: str) -> tuple[int, set[str]]:
    m = _RX_ANIO.fullmatch(texto)
    if not m:
        raise ValueError(f"celda de año ilegible {texto!r}")
    return int(m.group(1)), _marcas(m.group(2), f"el año {texto!r}")


def _parse_mes(texto: str) -> tuple[int, set[str]]:
    """'Agostoe' -> (8, {'e'}). El mes más largo reconocible, y el resto tienen que ser marcas.

    No alcanza con sacar las 'e' finales con un strip: 'Septiembre' termina en 'e' y es el mes
    sin marca. Se busca entonces el PREFIJO más largo que sea un mes, y sólo lo que sobra se
    interpreta como marca: 'Septiembree' -> ('septiembre', 'e').
    """
    t = texto.strip()
    for corte in range(len(t), 0, -1):
        n = meses.numero(t[:corte])
        if n is not None:
            return n, _marcas(t[corte:], f"el mes {texto!r}")
    raise ValueError(f"mes ilegible {texto!r}")


def _valor(celda, donde: str) -> float | None:
    """Número, o None si la celda está vacía (hueco legítimo). Texto en una celda de dato: falla."""
    if celda.ctype in (xlrd.XL_CELL_EMPTY, xlrd.XL_CELL_BLANK):
        return None
    if celda.ctype == xlrd.XL_CELL_NUMBER:
        return float(celda.value)
    if celda.ctype == xlrd.XL_CELL_TEXT and not celda.value.strip():
        return None
    raise ValueError(f"valor no numérico {celda.value!r} en {donde}")


def _fila_encabezado(hoja, encabezados: dict[int, str]) -> int:
    """Fila donde col0 dice 'Período' y cada columna leída trae su encabezado esperado."""
    for r in range(min(FILAS_ENCABEZADO, hoja.nrows)):
        if meses.normalizar(_texto(hoja.cell(r, 0))) != "periodo":
            continue
        recibido = {c: _texto(hoja.cell(r, c)) for c in encabezados}
        if all(meses.normalizar(recibido[c]) == meses.normalizar(e)
               for c, e in encabezados.items()):
            return r
        raise ValueError(f"encabezados inesperados en la fila {r}: {recibido}. "
                         f"Esperados: {encabezados}")
    raise ValueError(f"no se encontró la fila 'Período' en las primeras {FILAS_ENCABEZADO} filas")


def _validar_unidad(hoja, fila0: int, columnas, unidad: str) -> None:
    """Cada columna leída tiene que declarar `unidad` en alguna de las 3 filas bajo 'Período'."""
    esperada = meses.normalizar(unidad)
    for c in columnas:
        vistas = [_texto(hoja.cell(r, c)) for r in range(fila0 + 1, min(fila0 + 4, hoja.nrows))]
        if not any(meses.normalizar(v) == esperada for v in vistas):
            raise ValueError(f"columna {c}: no declara la unidad {unidad!r} "
                             f"(bajo el encabezado dice {vistas})")


def get_cuadro(url: str, columnas: dict[int, str], encabezados: dict[int, str],
               unidad: str | None = None,
               ) -> tuple[dict[str, list[tuple[dt.date, float, str]]], list[str]]:
    """Baja una planilla y la parte en una serie por columna.

    `columnas` mapea índice de columna -> slug de serie nuestro; `encabezados` el texto que esa
    columna tiene que traer en la fila de 'Período' (se valida: una columna corrida es la forma
    más fácil de cargar importaciones como exportaciones).

    Devuelve ({serie: [(fecha, valor, estado), ...]}, descartes). `estado` es 'provisorio' si el
    mes o su año traen alguna marca ('*' provisorio, 'e' estimado) y 'definitivo' si no. Levanta
    ValueError ante cualquier formato que no reconoce: ante la duda se falla y entra el respaldo.
    """
    resp = http.fetch(url, timeout=TIMEOUT)
    if not resp.content.startswith(FIRMA_OLE2):
        raise ValueError(f"{url} no es un .xls (content-type {resp.headers.get('content-type')!r}, "
                         f"empieza con {resp.content[:40]!r}). Probable página de error del "
                         f"INDEC servida con HTTP 200.")
    try:
        hoja = xlrd.open_workbook(file_contents=resp.content).sheet_by_index(0)
    except xlrd.XLRDError as e:
        raise ValueError(f"{url}: xls ilegible ({e})") from e

    fila0 = _fila_encabezado(hoja, encabezados)
    if unidad:
        _validar_unidad(hoja, fila0, columnas, unidad)
    series: dict[str, list[tuple[dt.date, float, str]]] = {s: [] for s in columnas.values()}
    descartes: list[str] = []
    anio: int | None = None
    marcas_anio: set[str] = set()
    fechas: list[dt.date] = []
    for r in range(fila0 + 1, hoja.nrows):
        txt_mes = _texto(hoja.cell(r, 1))
        if not txt_mes:
            # Filas vacías entre años, subencabezados de unidades y el pie ('Nota:', '*', 'e',
            # 'Fuente:' en col0). Ninguna trae mes, así que ninguna es dato.
            continue
        txt_anio = _texto(hoja.cell(r, 0))
        if txt_anio:
            anio, marcas_anio = _parse_anio(txt_anio)
        if anio is None:
            raise ValueError(f"fila {r}: mes {txt_mes!r} antes del primer año")
        n_mes, marcas_mes = _parse_mes(txt_mes)
        fecha = dt.date(anio, n_mes, 1)
        if not (1900 <= anio <= _anio_max()):
            descartes.append(f"fecha fuera de rango {fecha}")
            continue
        if fechas and fecha <= fechas[-1]:
            raise ValueError(f"fila {r}: {fecha} no es posterior a {fechas[-1]}; "
                             f"la planilla perdió el orden o repitió un mes")
        fechas.append(fecha)
        marcas = marcas_anio | marcas_mes
        estado = "provisorio" if marcas else "definitivo"
        for col, serie in columnas.items():
            valor = _valor(hoja.cell(r, col), f"fila {r} col {col} ({fecha:%Y-%m})")
            if valor is not None:
                series[serie].append((fecha, valor, estado))

    if len(fechas) < MIN_MESES:
        raise ValueError(f"{url} trajo {len(fechas)} meses (mínimo {MIN_MESES}). "
                         f"Planilla truncada o formato nuevo.")
    # Huecos: un mes faltante en el medio es un problema de lectura, no un dato. Mejor fallar y
    # caer a la API que cargar una serie agujereada que el X-13 después rechaza.
    for a, b in zip(fechas, fechas[1:]):
        siguiente = dt.date(a.year + a.month // 12, a.month % 12 + 1, 1)
        if b != siguiente:
            raise ValueError(f"{url}: salto de {a:%Y-%m} a {b:%Y-%m}, faltan meses")
    return series, descartes


if __name__ == "__main__":  # smoke test
    from . import config
    for nombre, cuadro in config.SERIES_XLS.items():
        series, desc = get_cuadro(cuadro["url"], cuadro["columnas"], cuadro["encabezados"])
        print(f"[{nombre}] descartes={len(desc)}")
        for serie, filas in sorted(series.items()):
            prov = sum(1 for *_, e in filas if e == "provisorio")
            print(f"  {serie:14} {len(filas):>4} meses  {filas[0][0]}..{filas[-1][0]}  "
                  f"provisorios={prov}  ult={filas[-1][1]:g} ({filas[-1][2]})")
