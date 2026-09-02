"""Lee el cuadro de parámetros de desestacionalización por serie (series_desest.toml).

Es la fuente única de qué series desestacionaliza cada dataset y con qué parámetros de
X-13. Cada `run.py` llama a `jobs_for(dataset)` al momento de desestacionalizar; agregar
una serie o un dataset es editar el TOML, no el código (ver el header del .toml).
"""
from __future__ import annotations

import os
import tomllib

_TOML = os.path.join(os.path.dirname(__file__), os.pardir, "series_desest.toml")

# Parámetros de X-13 que se pasan a seasonal.deseasonalize (el resto del job —tabla, vista,
# claves— lo arma cada run.py desde su config).
_PARAM_KEYS = ("mode", "td", "seasonalma")

# Parámetros OPCIONALES, con su default. Van aparte de `_PARAM_KEYS` a propósito: los de arriba
# son obligatorios y un bloque que se olvide de uno tiene que romper, mientras que éstos existen
# para una serie sola y pedírselos a los 15 bloques ya escritos sería ruido. El default de cada
# uno es "como corría el repo antes de que el parámetro existiera".
_PARAM_OPCIONALES = {"easter": 0}   # ancho del regresor de Pascua; 0 = sin regresor


def _load() -> dict:
    with open(_TOML, "rb") as f:
        return tomllib.load(f)


def datasets() -> list[str]:
    """Datasets declarados en el cuadro (orden del TOML)."""
    return list(_load().keys())


def _cfg(dataset: str) -> dict:
    data = _load()
    if dataset not in data:
        raise KeyError(f"'{dataset}' no está en {os.path.basename(_TOML)}")
    return data[dataset]


def table_for(dataset: str) -> str:
    """Tabla postgres (formato long) del dataset, según el cuadro."""
    return _cfg(dataset)["table"]


def jobs_for(dataset: str) -> list[tuple[str, dict]]:
    """[(serie, {mode, td, seasonalma, easter}), ...] para las series a desestacionalizar.

    El default del dataset se aplica a todas las series de `desest`; cada override
    [<dataset>.overrides.<serie>] pisa solo las claves que redefine.
    """
    cfg = _cfg(dataset)
    base = {k: cfg[k] for k in _PARAM_KEYS}
    base.update({k: cfg.get(k, d) for k, d in _PARAM_OPCIONALES.items()})
    admitidas = set(_PARAM_KEYS) | set(_PARAM_OPCIONALES)
    overrides = cfg.get("overrides", {})
    jobs = []
    for serie in cfg["desest"]:
        params = dict(base)
        params.update({k: v for k, v in overrides.get(serie, {}).items() if k in admitidas})
        jobs.append((serie, params))
    return jobs


def build_jobs(dataset: str, *, keep_dir=None) -> list[tuple[str, dict]]:
    """[(serie, kwargs_para_seasonal.deseasonalize), ...] listos para run_desest.

    Arma el job completo desde el cuadro: los params de X-13 (mode/td/seasonalma) más las
    columnas de la tabla long (todas las series se filtran por `serie`). La vista insumo sigue
    la convención `<table>_actual` (override opcional con la clave `view` en el TOML).
    """
    cfg = _cfg(dataset)
    table = cfg["table"]
    view = cfg.get("view", f"{table}_actual")
    overrides = cfg.get("overrides", {})
    ds_start = cfg.get("start")
    jobs = []
    for serie, params in jobs_for(dataset):
        start = overrides.get(serie, {}).get("start", ds_start)  # override por serie o default
        jobs.append((serie, dict(
            table=table, source_view=view,
            conflict_cols=("serie", "date"), extra_cols={"serie": serie},
            where="serie = %s", where_params=(serie,),
            start=start, keep_dir=keep_dir, **params)))
    return jobs
