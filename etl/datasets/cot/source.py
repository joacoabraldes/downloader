"""Fuente CFTC: Commitments of Traders, por dos caminos que NO son intercambiables.

  - **API Socrata** (`publicreporting.cftc.gov`): un request trae las últimas semanas ya
    parseadas. Es el camino del incremental. Pero **no tiene todo el histórico**: legacy arranca
    en 1998-01-06 y disaggregated en 2006-06-13.
  - **Zips anuales** (`cftc.gov/files/dea/history/`): el histórico completo, incluido el tramo
    1986-1997 de legacy que la API no tiene. Es el camino de `load-history`.

Tres diferencias de formato que hay que absorber, y ninguna es cosmética:

1. **Los encabezados de los zips difieren entre reportes.** Legacy usa espacios y paréntesis
   (`"Noncommercial Positions-Long (All)"`, 129 columnas); disaggregated usa guiones bajos
   (`"M_Money_Positions_Long_All"`, 191 columnas). Y el archivo adentro del zip se llama
   `annual.txt` en uno y `f_year.txt` en el otro, así que se toma el primer .txt que haya.

2. **La API renombra las columnas duplicadas.** El archivo tiene tres bloques (All / Old /
   Other) con los mismos nombres; Socrata desambigua con sufijos numéricos y en el camino le
   come el `_all` a algunas. El spread de managed money es `m_money_positions_spread`, NO
   `m_money_positions_spread_all` — ese último no existe y pedirlo devuelve None en silencio.

3. **La CFTC tiene un typo en su propio campo.** En legacy el spread es
   `noncomm_postions_spread_all`, sin la "i" de "positions". Está así en la API y hay que
   escribirlo mal a propósito.

Los valores vienen con espacios de relleno y a veces vacíos o con un punto: `_num` los limpia.
"""
from __future__ import annotations

import csv
import datetime as dt
import io
import json
import zipfile

from etl.core import http
from . import config

ZIP_BASE = "https://www.cftc.gov/files/dea/history/"
API_BASE = "https://publicreporting.cftc.gov/resource/"

# Columnas de interés, por layout de archivo y por API. El orden de las tuplas es siempre
# (fecha, codigo de contrato, interes abierto, largo, corto, spreading).
CAMPOS_ZIP = {
    "legacy": ("As of Date in Form YYYY-MM-DD", "CFTC Contract Market Code",
               "Open Interest (All)",
               "Noncommercial Positions-Long (All)",
               "Noncommercial Positions-Short (All)",
               "Noncommercial Positions-Spreading (All)"),
    "disagg": ("Report_Date_as_YYYY-MM-DD", "CFTC_Contract_Market_Code",
               "Open_Interest_All",
               "M_Money_Positions_Long_All",
               "M_Money_Positions_Short_All",
               "M_Money_Positions_Spread_All"),
}
CAMPOS_API = {
    # Ojo los dos nombres raros: el spread de managed money va sin `_all`, y el de legacy con
    # el typo de la CFTC ("postions"). Ver el docstring.
    "legacy": ("report_date_as_yyyy_mm_dd", "cftc_contract_market_code",
               "open_interest_all",
               "noncomm_positions_long_all",
               "noncomm_positions_short_all",
               "noncomm_postions_spread_all"),
    "disagg": ("report_date_as_yyyy_mm_dd", "cftc_contract_market_code",
               "open_interest_all",
               "m_money_positions_long_all",
               "m_money_positions_short_all",
               "m_money_positions_spread"),
}


class FormatoDesconocido(Exception):
    """El archivo no trae las columnas esperadas: la CFTC cambió el formato."""


def _num(v) -> float | None:
    """Entero de la CFTC: viene con relleno de espacios, y a veces vacío o con un punto."""
    if v is None:
        return None
    s = str(v).strip().replace(",", "")
    if s in ("", ".", "-"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _fecha(v) -> dt.date | None:
    """`2026-09-08` en los zips, `2026-09-08T00:00:00.000` en la API."""
    s = str(v).strip()[:10]
    try:
        return dt.date.fromisoformat(s)
    except ValueError:
        return None


def _filas(registros, campos, categoria: str, tipo: str, fuente: str, etiqueta: str):
    """Filtra por los códigos de contrato del TCR y arma las filas de la tabla.

    Unifica los códigos viejo y nuevo bajo el mismo `contrato`: los tres granos cambiaron de
    código en 1998 sin ningún solape (ver config.CONTRATOS).
    """
    f_fecha, f_cod, f_oi, f_largo, f_corto, f_spread = campos
    salida, vistas = [], 0
    for r in registros:
        codigo = str(r.get(f_cod, "")).strip()
        contrato = config.CODIGOS.get(codigo)
        if contrato is None:
            continue
        fecha = _fecha(r.get(f_fecha))
        if fecha is None:
            continue
        vistas += 1
        salida.append({
            "contrato": contrato, "categoria": categoria, "tipo": tipo, "date": fecha,
            "largo": _num(r.get(f_largo)), "corto": _num(r.get(f_corto)),
            "spreading": _num(r.get(f_spread)), "interes_abierto": _num(r.get(f_oi)),
            # Se guarda de qué código vino la fila y en qué unidad está: los granos cambiaron
            # de código Y de unidad el 1998-01-06 (ver config.UNIDAD_CAMBIA_DESDE).
            "codigo_cftc": codigo,
            "unidad": (config.UNIDAD_NUEVA
                       if fecha >= dt.date.fromisoformat(config.UNIDAD_CAMBIA_DESDE)
                       else config.UNIDAD_VIEJA),
            "fuente": fuente,
        })
    if not vistas:
        raise FormatoDesconocido(
            f"{etiqueta}: ninguno de los seis códigos de contrato apareció. "
            f"O cambió el formato o cambiaron los códigos.")
    return salida


def _validar_columnas(encabezado, campos, etiqueta: str) -> None:
    faltan = [c for c in campos if c not in encabezado]
    if faltan:
        raise FormatoDesconocido(f"{etiqueta}: faltan columnas {faltan}")


def bajar_zip(nombre: str, categoria: str, tipo: str, layout: str) -> list[dict]:
    """Baja un zip anual (o multianual) de la CFTC y devuelve las filas de los tres granos."""
    url = f"{ZIP_BASE}{nombre}.zip"
    resp = http.fetch(url, timeout=300)
    with zipfile.ZipFile(io.BytesIO(resp.content)) as z:
        # El archivo de adentro se llama distinto en cada reporte (annual.txt / f_year.txt /
        # FUT86_16.txt), así que se toma el único .txt en vez de adivinar el nombre.
        txts = [n for n in z.namelist() if n.lower().endswith(".txt")]
        if len(txts) != 1:
            raise FormatoDesconocido(f"{nombre}.zip: se esperaba un .txt y hay {len(txts)}")
        with z.open(txts[0]) as f:
            texto = io.TextIOWrapper(f, encoding="utf-8", errors="replace")
            lector = csv.DictReader(texto)
            campos = CAMPOS_ZIP[layout]
            _validar_columnas(lector.fieldnames or [], campos, f"{nombre}.zip")
            return _filas(lector, campos, categoria, tipo, url, f"{nombre}.zip")


def bajar_api(dataset: str, categoria: str, tipo: str, layout: str,
              desde: dt.date, limite: int = 50000) -> list[dict]:
    """Trae de la API Socrata las filas de los tres granos desde una fecha."""
    campos = CAMPOS_API[layout]
    codigos = ",".join(f"'{c}'" for c in config.CODIGOS)
    where = (f"cftc_contract_market_code in ({codigos})"
             f" AND report_date_as_yyyy_mm_dd >= '{desde.isoformat()}T00:00:00.000'")
    url = (f"{API_BASE}{dataset}.json?$where={where}"
           f"&$order=report_date_as_yyyy_mm_dd&$limit={limite}")
    resp = http.fetch(url, timeout=120)
    try:
        datos = resp.json()
    except json.JSONDecodeError as e:
        raise FormatoDesconocido(f"{dataset}: la respuesta no es JSON ({e})") from e
    if not datos:
        return []
    # La union de claves, no las de la primera fila: Socrata OMITE los campos nulos, asi que
    # una fila con un valor vacio haria fallar la validacion por un problema que no existe.
    presentes = set().union(*(set(r) for r in datos))
    _validar_columnas(presentes, campos, f"API {dataset}")
    return _filas(datos, campos, categoria, tipo, url, f"API {dataset}")
