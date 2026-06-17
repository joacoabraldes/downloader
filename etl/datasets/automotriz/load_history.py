"""Carga histórica (one-off) de las 3 series automotrices desde ind_automotriz.xlsx.

El xlsx tiene una hoja por serie (`produccion`, `ventas`, `expo`), cada una con
columnas `date` | valor. Inserta con estado=NULL (histórico), una fila por
(serie, date). Idempotente: usa insert_if_changed, así que re-correrlo no duplica.
"""
from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path

import openpyxl

from etl.core import db, report
from . import config

DEFAULT_XLSX = Path(__file__).parent / "data" / "ind_automotriz.xlsx"
FUENTE = "excel historico"


def read_series(path: str, serie: str) -> list[tuple[dt.date, float]]:
    """Lee (date, valor) de la hoja `serie`, salteando el header."""
    wb = openpyxl.load_workbook(path, data_only=True)
    ws = wb[serie]
    out = []
    for i, (fecha, valor) in enumerate(ws.iter_rows(values_only=True)):
        if i == 0:
            continue  # header: date | automotriz_*
        if fecha is None or valor is None:
            continue
        try:
            valor = float(valor)
        except (TypeError, ValueError):
            # El " - " es notación contable de CERO (p.ej. produccion abril-2020, COVID:
            # plantas cerradas). Lo tomamos como 0 para no dejar huecos en la serie (X-13
            # necesita meses contiguos). Cualquier otro no-numérico se saltea.
            if isinstance(valor, str) and valor.strip().strip("-") == "":
                valor = 0.0
            else:
                continue
        d = fecha.date() if hasattr(fecha, "date") else fecha
        out.append((dt.date(d.year, d.month, 1), valor))
    wb.close()
    return out


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="etl automotriz load-history",
                                 description="Carga histórica de ind_automotriz.xlsx.")
    ap.add_argument("--xlsx", default=str(DEFAULT_XLSX), help="ruta al Excel")
    ap.add_argument("--force", action="store_true", help="re-insertar aunque no cambie")
    args = ap.parse_args(argv)

    if not Path(args.xlsx).is_file():
        print(f"No se encontró {args.xlsx}.", file=sys.stderr)
        sys.exit(1)

    rep = report.Report("automotriz", "load-history")
    conn = db.get_conn()
    try:
        for serie in config.SERIES:
            rows = read_series(args.xlsx, serie)
            if not rows:
                rep.info(f"{serie}: sin filas")
                continue
            rep.info(f"{serie}: {len(rows)} filas | "
                     f"rango {rows[0][0]:%Y-%m}..{rows[-1][0]:%Y-%m}")
            for date, valor in rows:
                rep.tally(db.insert_if_changed(
                    conn, table=config.TABLE, key_cols=config.KEY_COLS,
                    key_vals=[serie, date], value_cols=config.VALUE_COLS,
                    row={"valor": valor}, estado=None, fuente=FUENTE, force=args.force,
                ))
    finally:
        conn.close()
    rep.summary()


if __name__ == "__main__":
    main()
