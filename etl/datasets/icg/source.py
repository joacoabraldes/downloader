"""Fuente UTDT: descarga + parseo de la planilla del Índice de Confianza en el Gobierno.

A diferencia del ICC, esta planilla está TRASPUESTA: los meses van en columnas, no en filas.
Dos hojas que se empalman sin solaparse ni pisarse ("Evolución ICG 2001-2022" y "Evolución
ICG a partir de 2023"): juntas dan la serie continua 2001-11 → hoy, sin huecos.

Layout de cada hoja (una fila por concepto):

    fila de fechas   |        | nov-01 | dic-01 | ...
    fila 'ICG'       |  ICG   |  1.036 |  0.757 | ...
    fila 'Variación' | Var ICG|        | -0.269 | ...

Se ancla en la fila cuya etiqueta (columna B) es "ICG" y se toma como fila de fechas la
primera fila POR ENCIMA que tenga fechas, en vez de fijar los índices 4 y 5. Así, si UTDT
agrega un título o una fila en blanco arriba, el parseo no se corre.

La fila "Variación ICG" se descarta: es derivada del nivel.

Trampa del último mes
---------------------
El mes recién publicado viene a veces TIPEADO COMO TEXTO en la fila de fechas ("jul-26") en
lugar de como fecha de Excel. Es justo el mes que este ETL existe para capturar: si se
ignoraran las celdas no numéricas, el ETL correría verde y llegaría siempre un mes tarde.
`utdt.celda_a_mes` parsea las dos formas.
"""
from __future__ import annotations

import datetime as dt

import xlrd

from etl.core import utdt
from . import config

# Columna B: ahí está la etiqueta de cada fila ('ICG', 'Variación ICG').
COL_ETIQUETA = 1
# Primera columna con datos (después de la etiqueta).
PRIMERA_COL_DATO = 2


def _fila_icg(hoja) -> int | None:
    """Índice de la fila de niveles: la etiquetada exactamente 'ICG' (no 'Variación ICG')."""
    for r in range(hoja.nrows):
        celda = hoja.cell(r, COL_ETIQUETA)
        if celda.ctype == xlrd.XL_CELL_TEXT and utdt.normalizar(celda.value) == "icg":
            return r
    return None


def _fila_fechas(hoja, fila_icg: int, datemode: int) -> int | None:
    """Fila de fechas: la primera arriba de `fila_icg` que tenga al menos 2 meses parseables."""
    for r in range(fila_icg - 1, -1, -1):
        meses = sum(1 for c in range(PRIMERA_COL_DATO, hoja.ncols)
                    if utdt.celda_a_mes(hoja.cell(r, c), datemode) is not None)
        if meses >= 2:
            return r
    return None


def parse_libro(libro) -> dict[dt.date, float]:
    """{mes -> valor del ICG} uniendo las dos hojas."""
    datos: dict[dt.date, float] = {}
    for hoja in libro.sheets():
        fila_icg = _fila_icg(hoja)
        if fila_icg is None:
            continue
        fila_fechas = _fila_fechas(hoja, fila_icg, libro.datemode)
        if fila_fechas is None:
            continue
        for c in range(PRIMERA_COL_DATO, hoja.ncols):
            valor = hoja.cell(fila_icg, c)
            if valor.ctype != xlrd.XL_CELL_NUMBER:
                continue
            fecha = utdt.celda_a_mes(hoja.cell(fila_fechas, c), libro.datemode)
            if fecha is not None:
                datos[fecha] = float(valor.value)
    return datos


def get_serie() -> tuple[dict[dt.date, float], str]:
    """Devuelve ({mes: valor}, url del .xls que se bajó)."""
    url = utdt.resolver_xls(config.ID_ITEM_MENU)
    return parse_libro(utdt.bajar_libro(url)), url


if __name__ == "__main__":  # smoke test
    datos, url = get_serie()
    meses = sorted(datos)
    print(url)
    print(f"{len(meses)} meses: {meses[0]}..{meses[-1]}")
    print(meses[-1], datos[meses[-1]])
