"""Fuente DNRPA: cuadro anual de transferencias por provincia y mes (HTML).

El Boletín Estadístico de la DNRPA expone un formulario (año x tipo de vehículo) que devuelve
una tabla de 24 provincias x 12 meses más la fila TOTAL. Un request trae el AÑO ENTERO, así
que el incremental no pide "el mes que falta": pide el año corriente y lee de ahí los meses de
la ventana. Es el mismo patrón que hidrocarburos, donde un GET trae la serie completa.

## El formulario es POST pero anda por GET

La página declara `<FORM METHOD=POST ACTION="tram_prov.php">`, pero el PHP lee los parámetros
por `$_REQUEST`: la misma consulta por query string devuelve exactamente el mismo HTML. Se usa
GET a propósito, para pasar por `etl.core.http.fetch` y heredar sus reintentos con backoff en
vez de escribir un POST a mano sin ninguna tolerancia a un corte.

Parámetros: `anio` (1995..año corriente), `codigo_tipo` (A=Autos, M=Motos, Q=Maquinarias),
`operacion=1`, `origen=portal_dnrpa`, `tipo_consulta=transferencias`.

## Sólo se guarda el TOTAL PAÍS

El cuadro trae el desagregado por provincia y NO se persiste. No es olvido: son 24 series más
por tipo de vehículo, ninguna se pidió, y cada una necesitaría su propia decisión de
desestacionalización. El total es la serie que se sigue. Si algún día hace falta la apertura,
el parser ya la tiene delante: es cambiar `_fila_total` por un barrido de todas las filas.

## Un mes sin publicar viene como 0, no como vacío

La tabla tiene siempre las 12 columnas: los meses que todavía no salieron valen `0`. Un cero
NO es un dato: en 380 meses el mínimo del total país es **18.034** (abril-2020, cuarentena) y el
siguiente más bajo 33.874 (diciembre-2001), así que el 0 se descarta y el mes queda como "no
publicado". Si se cargara tal cual, la serie tendría ceros al final y arrastraría el ajuste.

## La fila TOTAL se lee por su etiqueta, no por posición

Se busca la fila cuya primera celda dice TOTAL, no "la última fila". Es la misma lección que
`automotriz` con la columna del mes: la fuente puede sumar una provincia o una fila de pie sin
avisar, y anclar en la posición rompe en silencio.

Como control de que la tabla se leyó bien, se verifica que los 12 meses sumen la columna
`Total` del propio cuadro. Si no cierra, se corta: es la señal de que el layout cambió.
"""
from __future__ import annotations

import datetime as dt
import re

from bs4 import BeautifulSoup

from etl.core import http

BASE = "https://www.dnrpa.gov.ar/portal_dnrpa/estadisticas/rrss_tramites/tram_prov.php"
TIMEOUT = 60

# El cert de dnrpa.gov.ar valida bien (verificado 2026-09): acá NO va verify=False, a
# diferencia de las fuentes de energia.gob.ar.

MESES_ANIO = 12
# La fila trae 13 celdas de números: los 12 meses + el total del año.
CELDAS_TOTAL = MESES_ANIO + 1


class FormatoInesperado(RuntimeError):
    """El cuadro no tiene la forma que espera el parser. No se adivina: se corta."""


def consulta_url(anio: int, codigo_tipo: str) -> str:
    """URL de la consulta (es también el `fuente` que se guarda en cada fila)."""
    return (f"{BASE}?anio={anio}&codigo_tipo={codigo_tipo}&operacion=1"
            f"&origen=portal_dnrpa&tipo_consulta=transferencias")


def _numero(texto: str) -> int:
    """'153.361' -> 153361. El punto es separador de miles; no hay decimales en el cuadro."""
    limpio = texto.strip().replace(".", "")
    if not re.fullmatch(r"\d+", limpio):
        raise FormatoInesperado(f"celda no numérica en la fila TOTAL: {texto!r}")
    return int(limpio)


def _fila_total(html: str) -> list[int]:
    """Los 13 números de la fila TOTAL (12 meses + total del año)."""
    soup = BeautifulSoup(html, "lxml")
    for tabla in soup.find_all("table"):
        if "Provincia" not in tabla.get_text():
            continue
        for tr in tabla.find_all("tr"):
            celdas = tr.find_all(["th", "td"])
            if not celdas or celdas[0].get_text(strip=True).upper() != "TOTAL":
                continue
            nums = [_numero(c.get_text()) for c in celdas[1:]]
            if len(nums) != CELDAS_TOTAL:
                raise FormatoInesperado(
                    f"la fila TOTAL trae {len(nums)} números, esperaba {CELDAS_TOTAL}")
            return nums
    raise FormatoInesperado("no se encontró la fila TOTAL del cuadro por provincia")


def get_anio(anio: int, codigo_tipo: str) -> dict[dt.date, float]:
    """{primer día del mes: trámites} del año pedido. Los meses sin publicar no aparecen.

    Levanta `FormatoInesperado` si el cuadro no cierra: mejor cortar que cargar una serie
    tomada de la columna equivocada.
    """
    url = consulta_url(anio, codigo_tipo)
    resp = http.fetch(url, timeout=TIMEOUT)
    resp.encoding = resp.apparent_encoding
    nums = _fila_total(resp.text)
    meses, total_anio = nums[:MESES_ANIO], nums[MESES_ANIO]
    if sum(meses) != total_anio:
        raise FormatoInesperado(
            f"{anio}: los 12 meses suman {sum(meses)} y el cuadro dice {total_anio}")
    # El 0 es "todavía no publicado", no un dato (ver el docstring del módulo).
    return {dt.date(anio, m, 1): float(v) for m, v in enumerate(meses, 1) if v > 0}
