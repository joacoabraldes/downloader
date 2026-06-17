"""Exporta las series desestacionalizadas (d11 de X-13) a CSV.

Lee las vistas *_desest de la base y escribe un CSV por dataset:
  - automotriz -> automotriz_d11.csv  (formato ancho: date, produccion, ventas, expo)
  - granos     -> granos_d11.csv      (date, d11)
  - cemento    -> cemento_d11.csv     (date, d11)

Uso: `python -m etl export [datasets...] [--dir CARPETA]` (sin datasets = todos).
Corré antes el ETL/desest del dataset para tener los d11 al día en la base.
"""
from __future__ import annotations

import argparse
import csv
from pathlib import Path

from etl.core import db

ALL = ["granos", "cemento", "automotriz"]


def _write(path: Path, header: list[str], rows: list) -> int:
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)
    return len(rows)


def export_simple(conn, view: str, path: Path) -> int:
    """Series de un solo valor (granos / cemento): CSV date, d11."""
    with conn.cursor() as cur:
        cur.execute(f"select date, valor from {view} order by date")
        rows = cur.fetchall()
    return _write(path, ["date", "d11"], rows)


def export_automotriz(conn, path: Path) -> int:
    """3 series en formato ancho: date, produccion, ventas, expo."""
    with conn.cursor() as cur:
        cur.execute("select date, serie, valor from automotriz_desest "
                    "order by date, serie")
        wide: dict = {}
        for d, serie, valor in cur.fetchall():
            wide.setdefault(d, {})[serie] = valor
    rows = [[d, w.get("produccion", ""), w.get("ventas", ""), w.get("expo", "")]
            for d, w in sorted(wide.items())]
    return _write(path, ["date", "produccion", "ventas", "expo"], rows)


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="etl export",
                                 description="Exporta los d11 (desest) a CSV.")
    ap.add_argument("datasets", nargs="*", metavar="dataset",
                    help=f"datasets a exportar (default: todos). Opciones: {', '.join(ALL)}")
    ap.add_argument("--dir", default=".", help="carpeta de salida (default: actual)")
    args = ap.parse_args(argv)
    names = args.datasets or ALL
    unknown = [n for n in names if n not in ALL]
    if unknown:
        ap.error(f"dataset(s) desconocido(s): {', '.join(unknown)}")

    out = Path(args.dir)
    out.mkdir(parents=True, exist_ok=True)
    print("[export]")
    conn = db.get_conn()
    archivos = total = 0
    try:
        for name in names:
            if name == "automotriz":
                path = out / "automotriz_d11.csv"
                n = export_automotriz(conn, path)
            elif name == "granos":
                path = out / "granos_d11.csv"
                n = export_simple(conn, "molienda_granos_desest", path)
            else:  # cemento
                path = out / "cemento_d11.csv"
                n = export_simple(conn, "cemento_despacho_desest", path)
            archivos += 1
            total += n
            extra = "  (vacio: corriste la desest?)" if n == 0 else ""
            print(f"  {name}  filas={n}  -> {path}{extra}")
    finally:
        conn.close()
    print(f"resumen [export]  archivos={archivos}  filas={total}")


if __name__ == "__main__":
    main()
