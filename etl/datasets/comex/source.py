"""Fuente INDEC (ICA): las dos planillas .xls de índices de comercio exterior.

Las dos tienen el MISMO layout (una sola hoja, `sh_indec_comext_04`) y sólo cambian los
bloques de columnas:

    fila 0   título, con la base del índice ("... base 2004=100 ...")
    fila 2   encabezado de bloque, una etiqueta cada 4 columnas ('Nivel general',
             'Productos primarios', ... / 'Exportaciones', 'Importaciones')
    fila 3   subencabezado: Valor | Precio | Cantidad (la 4ª columna del bloque es un
             separador vacío)
    fila 5+  una fila por mes: col 0 el año (sólo en enero, con `*` si es provisorio),
             col 1 el mes en español, y los valores en las columnas de cada bloque

El bloque de exportaciones nivel general está en las dos planillas y es idéntico (verificado
sobre los 270 meses: diferencia 0). Se ingesta el de la planilla de expo —que además trae los
rubros— y el de comex se usa como CHEQUEO CRUZADO: si las dos dejan de coincidir, alguna
cambió de base o de metodología y el ETL tiene que gritar, no promediar.

El año provisorio viene marcado con asterisco en la celda del año ('2026*'), y la marca aplica
a los 12 meses de ese año. De ahí sale el `estado` de cada fila: 'provisorio' o 'definitivo'.
Cuando INDEC cierra un año, el asterisco desaparece y los mismos meses vuelven a entrar como
snapshot 'definitivo' (ver el modelo append-only de `etl.core.db`).
"""
from __future__ import annotations

import datetime as dt

import xlrd

from etl.core import http, meses
from . import config

URL_EXPO = "https://www.indec.gob.ar/ftp/cuadros/economia/serie_mensual_indices_expo.xls"
URL_COMEX = "https://www.indec.gob.ar/ftp/cuadros/economia/serie_mensual_indices_comex.xls"
HEADERS = {"User-Agent": "Mozilla/5.0 (comex ETL)"}
TIMEOUT = 120

TITULO_ROW = 0
GRUPO_ROW = 2       # etiqueta del bloque (rubro o flujo)
INDICE_ROW = 3      # Valor | Precio | Cantidad
FIRST_DATA_ROW = 5
ANIO_COL = 0
MES_COL = 1
FIRST_BLOCK_COL = 2  # las dos primeras columnas son el período (año, mes)

# Etiqueta del bloque (normalizada) -> (flujo, rubro). Una por planilla, porque en la de expo
# los bloques son los rubros y en la de comex son los flujos.
GRUPOS_EXPO = {
    "nivel general": ("expo", "general"),
    "productos primarios": ("expo", "primarios"),
    "manufacturas de origen agropecuario (moa)": ("expo", "moa"),
    "manufacturas de origen industrial (moi)": ("expo", "moi"),
    "combustibles y energia": ("expo", "combustibles"),
}
GRUPOS_COMEX = {
    # El bloque de exportaciones se lee igual (para el chequeo cruzado) pero NO se ingesta:
    # es el mismo nivel general de la otra planilla. Ver `get_latest`.
    "exportaciones": ("expo", "general"),
    "importaciones": ("impo", "general"),
}

# Tolerancia del chequeo cruzado entre planillas. Los índices vienen con ~10 decimales y
# salen del mismo cálculo: cualquier diferencia real es un cambio de base o de metodología.
TOL_CRUCE = 1e-6


def _norm(texto) -> str:
    """Etiqueta de encabezado normalizada (minúsculas, sin acentos, sin espacios de borde)."""
    return meses.normalizar(str(texto))


def download(url: str) -> bytes:
    """Baja una de las planillas."""
    r = http.fetch(url, headers=HEADERS, timeout=TIMEOUT)
    return r.content


def _check_base(sh) -> None:
    """Corta si el título ya no declara la base que tenemos cargada.

    Un rebase (INDEC ya pasó de base 1993 a base 2004) hace que los valores nuevos NO sean
    comparables con los viejos. Apendearlos en la misma serie sería peor que no traer el dato:
    quedaría un salto de nivel sin marcar. Se prefiere fallar ruidoso y decidir a mano.
    """
    titulo = _norm(sh.cell_value(TITULO_ROW, 0))
    if _norm(config.BASE) not in titulo:
        raise ValueError(
            f"la planilla ya no declara base {config.BASE}: {sh.cell_value(TITULO_ROW, 0)!r}. "
            "Si INDEC rebaseó, la serie cargada no es comparable con la nueva: revisar antes "
            "de seguir ingestando.")


def _bloques(sh) -> list[tuple[str, dict[str, int]]]:
    """[(etiqueta_cruda, {'valor': col, 'precio': col, 'cantidad': col}), ...].

    Se leen del encabezado en vez de hardcodear las columnas: si INDEC agrega un rubro o
    reordena los bloques, el parser sigue ubicándolos (y `get_latest` avisa de los que no
    conoce en vez de leer la columna equivocada en silencio).
    """
    out: list[tuple[str, dict[str, int]]] = []
    etiqueta, cols = None, {}
    for c in range(FIRST_BLOCK_COL, sh.ncols):
        grupo = str(sh.cell_value(GRUPO_ROW, c)).strip()
        if grupo:
            if etiqueta and cols:
                out.append((etiqueta, cols))
            etiqueta, cols = grupo, {}
        indice = _norm(sh.cell_value(INDICE_ROW, c))
        if etiqueta and indice in config.INDICES:
            cols[indice] = c
    if etiqueta and cols:
        out.append((etiqueta, cols))
    return out


def _periodos(sh) -> list[tuple[int, dt.date, str]]:
    """[(fila, fecha primer día del mes, estado)] de las filas mensuales.

    El año aparece sólo en la fila de enero y arrastra hasta el enero siguiente; el `*` de esa
    celda marca el año entero como provisorio. Las filas sin mes reconocible (separadores,
    notas al pie) se saltean.
    """
    out: list[tuple[int, dt.date, str]] = []
    anio, estado = None, "definitivo"
    for r in range(FIRST_DATA_ROW, sh.nrows):
        celda = str(sh.cell_value(r, ANIO_COL)).strip()
        if celda:
            crudo = celda.replace("*", "").strip()
            if not crudo.isdigit():
                continue  # nota al pie ("* Dato provisorio.", "Fuente: ...")
            anio, estado = int(crudo), ("provisorio" if "*" in celda else "definitivo")
        mes = meses.numero(str(sh.cell_value(r, MES_COL)))
        if anio is None or mes is None:
            continue
        out.append((r, dt.date(anio, mes, 1), estado))
    return out


def parse(blob: bytes, grupos: dict[str, tuple[str, str]]) -> tuple[
        dict[str, dict[dt.date, float]], dict[dt.date, str], list[str]]:
    """Parsea una planilla.

    Devuelve `({serie: {fecha: valor}}, {fecha: estado}, [etiquetas de bloque desconocidas])`.
    Las etiquetas que no están en `grupos` no se ingestan (no sabríamos qué serie son) pero se
    devuelven para que el run las reporte como falla: un rubro nuevo del INDEC tiene que
    aparecer en el mail del cron, no perderse.
    """
    sh = xlrd.open_workbook(file_contents=blob).sheet_by_index(0)
    _check_base(sh)
    periodos = _periodos(sh)
    estados = {fecha: estado for _, fecha, estado in periodos}
    data: dict[str, dict[dt.date, float]] = {}
    desconocidos: list[str] = []
    for etiqueta, cols in _bloques(sh):
        destino = grupos.get(_norm(etiqueta))
        if destino is None:
            desconocidos.append(etiqueta)
            continue
        flujo, rubro = destino
        for indice, col in cols.items():
            serie = f"{flujo}_{indice}_{rubro}"
            valores = data.setdefault(serie, {})
            for fila, fecha, _ in periodos:
                v = sh.cell_value(fila, col)
                if isinstance(v, (int, float)) and v != "":
                    valores[fecha] = float(v)
    return data, estados, desconocidos


def _cruce(expo: dict[str, dict[dt.date, float]],
           comex: dict[str, dict[dt.date, float]]) -> list[str]:
    """Meses en que el nivel general de expo NO coincide entre las dos planillas.

    Es el chequeo cruzado: las dos publican el mismo bloque y tienen que dar igual. Si no dan,
    una de las dos cambió (base, metodología, o el parser se corrió de columna) y el dato de
    importaciones —que sale sólo de la de comex— deja de ser confiable.
    """
    fallas = []
    for serie in (f"expo_{i}_general" for i in config.INDICES):
        a, b = expo.get(serie, {}), comex.get(serie, {})
        for fecha in sorted(set(a) & set(b)):
            if abs(a[fecha] - b[fecha]) > TOL_CRUCE:
                fallas.append(f"{serie} {fecha:%Y-%m}: expo={a[fecha]} comex={b[fecha]}")
    return fallas


def get_latest() -> tuple[dict[str, dict[dt.date, float]], dict[dt.date, str], list[str], str]:
    """`({serie: {fecha: valor}}, {fecha: estado}, [avisos], fuente)` de las dos planillas.

    Las 15 series de exportación salen de la planilla de expo y las 3 de importación de la de
    comex. El bloque de exportaciones de comex se parsea sólo para cruzarlo y se descarta.
    `avisos` junta los bloques desconocidos y las diferencias del cruce: el run los reporta
    como falla de la corrida sin tirar abajo lo que sí se pudo traer.
    """
    d_expo, est_expo, desc_expo = parse(download(URL_EXPO), GRUPOS_EXPO)
    d_comex, est_comex, desc_comex = parse(download(URL_COMEX), GRUPOS_COMEX)

    avisos = [f"bloque desconocido en {URL_EXPO.rsplit('/', 1)[-1]}: {e!r}" for e in desc_expo]
    avisos += [f"bloque desconocido en {URL_COMEX.rsplit('/', 1)[-1]}: {e!r}" for e in desc_comex]
    diferencias = _cruce(d_expo, d_comex)
    if diferencias:
        avisos.append(f"las dos planillas no coinciden en el nivel general de expo "
                      f"({len(diferencias)} meses); primera: {diferencias[0]}")

    data = dict(d_expo)
    data.update({s: v for s, v in d_comex.items() if s.startswith("impo_")})
    # Los estados salen de la planilla de expo y se completan con los meses que sólo tenga la
    # de comex (no debería haber: publican juntas y con el mismo corte).
    estados = dict(est_comex)
    estados.update(est_expo)
    return data, estados, avisos, f"{URL_EXPO} + {URL_COMEX}"


if __name__ == "__main__":  # smoke test
    data, estados, avisos, fuente = get_latest()
    print(fuente)
    for a in avisos:
        print(f"  AVISO {a}")
    fechas = sorted(estados)
    print(f"series: {len(data)}  meses: {fechas[0]:%Y-%m}..{fechas[-1]:%Y-%m}")
    for serie in sorted(data):
        ultimo = max(data[serie])
        print(f"  {serie:28} n={len(data[serie]):4}  {ultimo:%Y-%m}={data[serie][ultimo]:.2f}"
              f"  ({estados[ultimo]})")
