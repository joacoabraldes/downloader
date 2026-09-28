"""Fuente UTDT: aperturas del ICG calculadas desde los microdatos de la encuesta (.dta).

La misma página de descarga del ICG publica, junto a la planilla, la base de microdatos
"Microdatos Encuestas Mensuales ICG, 2001 - Presente (Stata)": un .dta de ~22 MB con una fila
por encuestado (~1000-1200 por mes, ~316k en total) y su libro de códigos en PDF. El link se
resuelve en cada corrida igual que el del .xls (el `fname` cambia con cada publicación).

Cada apertura es la media ponderada, por mes, de una variable 0-5:

  - componentes: las 5 preguntas recodificadas a 0/5 (`*_rec`, ver `config.COMPONENTES`);
  - cortes: el ICG individual (`ICG`, promedio de las 5) dentro de cada categoría de zona,
    sexo, edad y nivel educativo (`config.CORTES`).

Mes: `ola`, no `año`/`mes`
--------------------------
`año`/`mes`/`dia` son la fecha en que se hizo CADA encuesta; `ola` es el mes al que pertenece
la medición (libro de códigos). No coinciden: en ~30 meses el campo arranca los últimos días
del mes anterior (p.ej. ola 2011-08 con 335 casos fechados en julio), y 17 filas no tienen
fecha. Agrupando por `año`/`mes` el ICG total difiere de la planilla en 70 de 299 meses;
agrupando por `ola` coincide en todos salvo 10 (diferencias históricas de la fuente, ver
`run.py`).

Ponderación
-----------
`ponderacion_UTDT`, que es la que el libro de códigos manda aplicar a toda estadística. El
.dta trae también `PON_ESTR`, `pon_estr`, `pondef` y `pond_edu`, pero sólo cubren olas viejas
(`PON_ESTR` tiene ~1200 valores de 316k). Una fila sin valor o sin peso se descarta SÓLO para
la variable que le falta (p.ej. las 204 filas sin `edu` siguen contando en los componentes).
"""
from __future__ import annotations

import datetime as dt
import io
import math
from typing import NamedTuple

import pandas as pd

from etl.core import utdt
from . import config

# Mes de la medición (ver docstring).
COL_OLA = "ola"
# ICG individual (promedio de los 5 componentes 0/5).
COL_ICG = "ICG"


class Microdatos(NamedTuple):
    url: str
    aperturas: dict[str, dict[dt.date, float]]   # {serie: {mes: valor}}
    total: dict[dt.date, float]                  # ICG total recalculado (sólo para el control)
    descartadas: list[tuple[str, dt.date, int]]  # (serie, mes, casos) bajo config.MIN_CASOS
    avisos: list[str]                            # códigos de corte sin serie asignada, etc.


def _columnas() -> list[str]:
    return [COL_OLA, COL_ICG, config.PESO, *config.COMPONENTES.values(), *config.CORTES]


def leer_dta(contenido: bytes) -> pd.DataFrame:
    """Lee sólo las columnas que se usan, con los códigos numéricos (sin etiquetas Stata)."""
    df = pd.read_stata(io.BytesIO(contenido), columns=_columnas(),
                       convert_categoricals=False)
    # `ola` viene como fecha de Stata (%tm); se normaliza al primer día del mes.
    df[COL_OLA] = pd.to_datetime(df[COL_OLA]).dt.to_period("M").dt.to_timestamp().dt.date
    return df


def _media_ponderada(df: pd.DataFrame, valor: str, grupos: list[str]) -> pd.DataFrame:
    """Media ponderada de `valor` y casos sin ponderar, por `grupos`."""
    d = df[[*grupos, valor, config.PESO]].dropna()
    d = d.assign(_producto=d[valor] * d[config.PESO])
    g = d.groupby(grupos)
    return pd.DataFrame({"media": g["_producto"].sum() / g[config.PESO].sum(),
                         "casos": g.size()})


def calcular(df: pd.DataFrame) -> tuple[dict, dict, list, list]:
    """(aperturas, total, descartadas, avisos) a partir del DataFrame de microdatos."""
    aperturas: dict[str, dict[dt.date, float]] = {s: {} for s in config.APERTURAS}
    descartadas: list[tuple[str, dt.date, int]] = []
    avisos: list[str] = []

    def guardar(serie: str, mes: dt.date, media: float, casos: int) -> None:
        if casos < config.MIN_CASOS:
            descartadas.append((serie, mes, int(casos)))
        elif not math.isfinite(media):
            # Suma de pesos nula o degenerada: un NaN no se puede comparar contra el snapshot
            # previo y se reinsertaría en cada corrida.
            avisos.append(f"{serie} {mes:%Y-%m}: media no finita ({media}), no se carga")
        else:
            aperturas[serie][mes] = float(media)

    total = {mes: float(f.media)
             for mes, f in _media_ponderada(df, COL_ICG, [COL_OLA]).iterrows()}

    for serie, var in config.COMPONENTES.items():
        for mes, f in _media_ponderada(df, var, [COL_OLA]).iterrows():
            guardar(serie, mes, f.media, f.casos)

    for var, codigos in config.CORTES.items():
        sin_serie = set()
        for (mes, codigo), f in _media_ponderada(df, COL_ICG, [COL_OLA, var]).iterrows():
            serie = codigos.get(int(codigo))
            if serie is None:
                sin_serie.add(int(codigo))
                continue
            guardar(serie, mes, f.media, f.casos)
        if sin_serie:
            avisos.append(f"{var}: códigos sin serie asignada {sorted(sin_serie)} (ignorados)")

    return aperturas, total, descartadas, avisos


def get_aperturas() -> Microdatos:
    """Resuelve el link del .dta, lo baja (~22 MB, en memoria) y calcula las aperturas."""
    url = utdt.resolver_descarga(config.ID_ITEM_MENU, "dta")
    resp = utdt.SESSION.get(url, timeout=utdt.TIMEOUT)
    resp.raise_for_status()
    try:
        df = leer_dta(resp.content)
    except ValueError as e:
        # read_stata tira ValueError si falta alguna columna pedida: cambió el layout del .dta.
        raise RuntimeError(f"el .dta no trae las columnas esperadas ({e})") from e
    if df.empty:
        raise RuntimeError("el .dta no trae filas")
    return Microdatos(url, *calcular(df))


if __name__ == "__main__":  # smoke test
    m = get_aperturas()
    print(m.url)
    meses = sorted(m.total)
    print(f"{len(meses)} meses: {meses[0]}..{meses[-1]}")
    for serie, datos in m.aperturas.items():
        ultimo = max(datos)
        print(f"  {serie:24s} {len(datos):4d} meses  {ultimo}  {datos[ultimo]:.3f}")
    print("descartadas:", m.descartadas)
    print("avisos:", m.avisos)
