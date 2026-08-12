"""Fuente UTDT: descarga + parseo de la planilla del Índice de Confianza del Consumidor.

La planilla trae la serie COMPLETA (1998-07 →) en dos hojas, ambas con la misma forma:
columna A = mes, y después pares (nivel, variación mensual) por serie.

  "Desagregación por regiones"    -> Capital | Interior | GBA | ICC Nacional
  "Desagregación por subíndices"  -> Situación Personal | Situación Macro |
                                     Bienes Durables e Inmuebles | ICC Nacional

Las columnas de "Variación mensual" se descartan: son derivadas del nivel y se recalculan en
un select. Guardamos sólo niveles.

`nacional` sale SÓLO de la hoja de regiones
-------------------------------------------
Las dos hojas repiten la columna "ICC Nacional" y NO coinciden en 24 meses del tramo viejo.
Casi todas son diferencias de redondeo (39.032707 vs 39.03), pero algunas son de verdad:
2009-06 da 40.206398 en regiones y 40.007807 en subíndices. Tomar la que caiga última dejaría
el valor a merced del orden de las hojas, así que se fija la de regiones, que es la hoja más
larga y la que arranca antes.

Las columnas se buscan POR ENCABEZADO (filas 2 y 3 de la planilla), no por posición fija: si
UTDT intercala una serie nueva, el ETL sigue leyendo bien en vez de correrse una columna.
"""
from __future__ import annotations

import datetime as dt

import xlrd

from etl.core import utdt
from . import config

# Filas del encabezado que, unidas, nombran cada columna. La fila 1 es el título general
# ("ICC Nacional - Desagregación por regiones") y se saltea a propósito: contiene la palabra
# "Nacional" y ensuciaría el match de todas las columnas de esa hoja.
FILAS_ENCABEZADO = (1, 2)

# Encabezado normalizado -> nombre de serie. Se busca por substring.
ETIQUETAS = {
    "capital": "capital",
    "interior": "interior",
    "gba": "gba",
    "nacional": "nacional",
    "situacion personal": "situacion_personal",
    "situacion macro": "situacion_macro",
    "bienes durables": "bienes_durables",
}

# Hoja -> series que se toman de ella. `nacional` se lee sólo de regiones (ver docstring).
HOJAS = {
    "regiones": {"capital", "interior", "gba", "nacional"},
    "subindices": {"situacion_personal", "situacion_macro", "bienes_durables"},
}


def _que_hoja(nombre: str) -> str | None:
    n = utdt.normalizar(nombre)
    if "region" in n:
        return "regiones"
    if "subindice" in n:
        return "subindices"
    return None


def columnas(hoja) -> dict[int, str]:
    """{índice de columna -> serie} leyendo los encabezados de la hoja."""
    encontradas: dict[int, str] = {}
    for c in range(1, hoja.ncols):
        partes = []
        for f in FILAS_ENCABEZADO:
            if f < hoja.nrows:
                celda = hoja.cell(f, c)
                if celda.ctype == xlrd.XL_CELL_TEXT:
                    partes.append(celda.value)
        encabezado = utdt.normalizar(" ".join(partes))
        if not encabezado or "variacion" in encabezado:
            continue  # columna derivada (variación mensual), no la guardamos
        for etiqueta, serie in ETIQUETAS.items():
            if etiqueta in encabezado:
                encontradas[c] = serie
                break
    return encontradas


def parse_libro(libro) -> dict[dt.date, dict[str, float]]:
    """{mes -> {serie: valor}} con los niveles de las dos hojas."""
    datos: dict[dt.date, dict[str, float]] = {}
    for hoja in libro.sheets():
        cual = _que_hoja(hoja.name)
        if cual is None:
            continue
        cols = {c: s for c, s in columnas(hoja).items() if s in HOJAS[cual]}
        for r in range(hoja.nrows):
            fecha = utdt.celda_a_mes(hoja.cell(r, 0), libro.datemode)
            if fecha is None:
                continue
            fila = datos.setdefault(fecha, {})
            for c, serie in cols.items():
                celda = hoja.cell(r, c)
                if celda.ctype == xlrd.XL_CELL_NUMBER:
                    fila[serie] = float(celda.value)
    return datos


def get_serie() -> tuple[dict[dt.date, dict[str, float]], str]:
    """Devuelve ({mes: {serie: valor}}, url del .xls que se bajó)."""
    url = utdt.resolver_xls(config.ID_ITEM_MENU)
    return parse_libro(utdt.bajar_libro(url)), url


if __name__ == "__main__":  # smoke test
    datos, url = get_serie()
    meses = sorted(datos)
    print(url)
    print(f"{len(meses)} meses: {meses[0]}..{meses[-1]}")
    print(meses[-1], datos[meses[-1]])
