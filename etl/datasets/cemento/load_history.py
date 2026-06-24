"""Carga histórica de la serie de despacho de cemento desde cemento.xlsx.

Inserta todas las filas del xlsx con estado=NULL (no tienen estado provisorio/
definitivo), excluyendo abril 2026, que se vuelve a cargar vía scraping para
corregir el error de carga manual (quedó con el provisorio en vez del definitivo).

Idempotente: no reinserta una fecha histórica que ya esté cargada.

NOTA: dejá `cemento.xlsx` en `etl/datasets/cemento/data/` (no se versiona el xlsx).
"""
from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

import openpyxl

from etl.core import db, report
from . import config

DEFAULT_XLSX = Path(__file__).parent / "data" / "cemento.xlsx"
EXCLUDE = {date(2026, 4, 1)}  # abril 2026 se carga vía scraping
FUENTE = None


def read_rows(path: str):
    """Lee (fecha, valor) del xlsx. La hoja no tiene header."""
    wb = openpyxl.load_workbook(path, data_only=True)
    ws = wb.active
    rows = []
    for fecha, valor in ws.iter_rows(values_only=True):
        if fecha is None or valor is None:
            continue
        d = fecha.date() if hasattr(fecha, "date") else fecha
        rows.append((d, float(valor)))
    wb.close()
    return rows


def main(argv=None):
    ap = argparse.ArgumentParser(prog="etl cemento load-history",
                                 description="Carga histórica desde cemento.xlsx")
    ap.add_argument("--xlsx", default=str(DEFAULT_XLSX), help="ruta al xlsx")
    ap.add_argument("--force", action="store_true", help="re-insertar aunque no cambie")
    args = ap.parse_args(argv)

    if not Path(args.xlsx).is_file():
        print(f"No se encontró {args.xlsx}. Copiá cemento.xlsx en "
              f"etl/datasets/cemento/data/.", file=sys.stderr)
        sys.exit(1)

    rows = read_rows(args.xlsx)
    rep = report.Report("cemento", "load-history")
    rep.info(f"Excel: {len(rows)} filas (serie {config.MAIN_SERIE}) | "
             f"excluido(abril-2026, se carga por scraping): 1")
    conn = db.get_conn()
    try:
        # El xlsx histórico solo tiene la serie principal (despacho_nacional); las otras 3
        # series se llenan vía scraping de los 'definitivo'.
        for fecha, valor in rows:
            if fecha in EXCLUDE:
                continue
            rep.tally(db.insert_if_changed(
                conn, table=config.TABLE, key_cols=config.KEY_COLS,
                key_vals=[config.MAIN_SERIE, fecha], value_cols=config.VALUE_COLS,
                row={"valor": valor}, estado=None, fuente=FUENTE, force=args.force,
            ))
    finally:
        conn.close()
    rep.summary()


if __name__ == "__main__":
    main()
