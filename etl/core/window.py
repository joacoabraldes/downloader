"""Ventana de meses a procesar en el ETL incremental.

Problema que resuelve: anclar la ventana a "hoy" (`--months-back N`) puede dejar afuera el
último mes publicado si la fuente publica con atraso. Default nuevo: **ponerse al día desde
el último mes que hay en la base** (re-chequeando los últimos `lookback` meses por si hubo
revisiones) hasta el mes actual. `--month` y `--months-back` siguen como override.
"""
from __future__ import annotations

import datetime as dt

from . import db

# Excluye la serie desestacionalizada al mirar "hasta dónde llegó" la serie observada.
OBSERVED = "estado is distinct from 'desestacionalizado'"
MAX_MONTHS = 24  # tope de seguridad para una base muy atrasada


def add_months(d: dt.date, n: int) -> dt.date:
    idx = d.year * 12 + (d.month - 1) + n
    return dt.date(idx // 12, idx % 12 + 1, 1)


def month_range(a: dt.date, b: dt.date) -> list[dt.date]:
    """Meses (primer día) de `a` a `b` inclusive, ascendente."""
    out, d = [], a
    while d <= b:
        out.append(d)
        d = add_months(d, 1)
    return out


def catch_up_start(conn, *, table, lookback=2, where=OBSERVED, where_params=(),
                   today=None) -> dt.date:
    """Primer mes a (re)procesar: min(último mes en la base, hoy-lookback), con tope.

    Re-chequea los últimos `lookback` meses por revisiones y, si la base está atrasada,
    arranca desde su último mes (acotado por MAX_MONTHS).
    """
    today = today or dt.date.today()
    cur = dt.date(today.year, today.month, 1)
    floor = add_months(cur, -lookback)
    last = db.last_date(conn, table=table, where=where, where_params=where_params)
    start = min(last, floor) if last else floor
    return max(start, add_months(cur, -(MAX_MONTHS - 1)))  # tope de seguridad


def target_months(conn, *, table, month=None, months_back=None, lookback=2,
                  where=OBSERVED, where_params=(), today=None) -> list[dt.date]:
    """Lista de meses a procesar (ascendente).

    - `--month YYYY-MM`  -> sólo ese mes.
    - `--months-back N`  -> últimos N meses desde hoy.
    - default            -> desde min(último mes en la base, hoy-lookback) hasta hoy.
    """
    today = today or dt.date.today()
    cur = dt.date(today.year, today.month, 1)
    if month:
        y, m = map(int, month.split("-"))
        return [dt.date(y, m, 1)]
    if months_back is not None:
        return month_range(add_months(cur, -(months_back - 1)), cur) if months_back > 0 else []
    return month_range(catch_up_start(conn, table=table, lookback=lookback,
                                      where=where, where_params=where_params, today=today), cur)
