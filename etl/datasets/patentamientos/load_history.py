"""Carga histórica (one-off) de patentamientos 4W desde PDFs ya bajados.

SIOMAA no deja pedir meses arbitrarios por URL, así que el histórico se carga desde los
PDFs de los informes ya descargados (~53 archivos, ene-2022 en adelante). Se parsea cada
PDF con el mismo parser del ETL (Tabla 1), tomando el mes/año del propio encabezado del
PDF (no del nombre de archivo, que varía mucho). Idempotente: usa insert_if_changed.

Uso:
  python -m etl patentamientos load-history --dir /ruta/a/reports_4w
"""
from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path

from etl.core import db, report
from . import config, source

FUENTE = "siomaa 4w historico"


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="etl patentamientos load-history",
                                 description="Carga histórica desde PDFs de SIOMAA (one-off).")
    ap.add_argument("--dir", required=True, help="carpeta con los PDFs de los informes 4W")
    ap.add_argument("--force", action="store_true", help="re-insertar aunque no cambie")
    args = ap.parse_args(argv)

    pdfs = sorted(Path(args.dir).glob("*.pdf"))
    if not pdfs:
        print(f"No se encontraron PDFs en {args.dir}", file=sys.stderr)
        sys.exit(1)

    rep = report.Report("patentamientos", "load-history")
    rep.info(f"PDFs: {len(pdfs)} en {args.dir}")
    conn = db.get_conn()
    try:
        for path in pdfs:
            try:
                year, month, data = source.parse_report(path.read_bytes())
            except Exception as e:
                rep.note(path.name, f"ERROR parseando: {e}", status="saltado")
                continue
            if not year or not month or not data:
                rep.note(path.name, "sin Tabla 1 / sin período", status="saltado")
                continue
            fecha = dt.date(year, month, 1)
            for serie in config.SERIES:
                valor = data.get(serie)
                rep.tally(db.insert_if_changed(
                    conn, table=config.TABLE, key_cols=config.KEY_COLS,
                    key_vals=[serie, fecha], value_cols=config.VALUE_COLS,
                    row={"valor": None if valor is None else float(valor)},
                    estado="provisorio", fuente=FUENTE, force=args.force,
                ))
    finally:
        conn.close()
    rep.summary()


if __name__ == "__main__":
    main()
