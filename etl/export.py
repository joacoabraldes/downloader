"""Exporta las series desestacionalizadas (d11 de X-13) a CSV.

Lee las vistas *_desest de la base y escribe un CSV por dataset:
  - automotriz     -> automotriz_d11.csv      (formato ancho: date, produccion, ventas, expo)
  - patentamientos -> patentamientos_d11.csv  (formato ancho: date + una col por categoría)
  - granos         -> granos_d11.csv          (date, d11)
  - cemento        -> cemento_d11.csv          (date, d11)
  - acero          -> acero_d11.csv            (date, d11)
  - aves           -> aves_d11.csv             (date, d11)
  - leche          -> leche_d11.csv            (date, d11)
  - bovinos        -> bovinos_d11.csv          (date, d11)
  - demanda_energia-> demanda_energia_d11.csv  (date, d11)  (serie no_residencial)
  - hidrocarburos  -> hidrocarburos_d11.csv    (formato ancho: date, petroleo, gas)
  - ventas_combustibles -> ventas_combustibles_d11.csv (ancho: date, gasoil, nafta, glp, total_automotor)
  - escrituras_caba-> escrituras_caba_d11.csv  (date, d11)  (serie compraventa)
  - comex          -> comex_d11.csv            (formato ancho: date + las 6 series de cantidad)

Uso: `python -m etl export [datasets...] [--dir CARPETA]` (sin datasets = todos).
Corré antes el ETL/desest del dataset para tener los d11 al día en la base.
"""
from __future__ import annotations

import argparse
import csv
from pathlib import Path

from etl.core import db

ALL = ["granos", "cemento", "automotriz", "patentamientos", "acero", "aves", "leche",
       "bovinos", "demanda_energia", "hidrocarburos", "ventas_combustibles",
       "escrituras_caba", "comex"]


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


def export_wide(conn, view: str, series: list[str], path: Path) -> int:
    """Varias series en formato ancho: date + una columna por serie (en el orden dado)."""
    with conn.cursor() as cur:
        cur.execute(f"select date, serie, valor from {view} order by date, serie")
        wide: dict = {}
        for d, serie, valor in cur.fetchall():
            wide.setdefault(d, {})[serie] = valor
    rows = [[d] + [w.get(s, "") for s in series] for d, w in sorted(wide.items())]
    return _write(path, ["date"] + series, rows)


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
                from .datasets.automotriz import config as ac
                path = out / "automotriz_d11.csv"
                n = export_wide(conn, "etl_automotriz_desest", ac.SERIES, path)
            elif name == "patentamientos":
                from .datasets.patentamientos import config as pc
                path = out / "patentamientos_d11.csv"
                n = export_wide(conn, "etl_patentamientos_desest", pc.SERIES, path)
            elif name == "granos":
                path = out / "granos_d11.csv"
                n = export_simple(conn, "etl_molienda_granos_desest", path)
            elif name == "acero":
                path = out / "acero_d11.csv"
                n = export_simple(conn, "etl_acero_desest", path)
            elif name == "aves":
                path = out / "aves_d11.csv"
                n = export_simple(conn, "etl_aves_desest", path)
            elif name == "leche":
                path = out / "leche_d11.csv"
                n = export_simple(conn, "etl_leche_desest", path)
            elif name == "bovinos":
                path = out / "bovinos_d11.csv"
                n = export_simple(conn, "etl_bovinos_desest", path)
            elif name == "comex":
                from .datasets.comex import config as cc
                path = out / "comex_d11.csv"
                n = export_wide(conn, "etl_comex_desest", cc.DESEST_SERIES, path)
            elif name == "hidrocarburos":
                # Solo los dos totales se desestacionalizan (el desagregado por tipo, no).
                from .datasets.hidrocarburos import config as hc
                path = out / "hidrocarburos_d11.csv"
                n = export_wide(conn, "etl_hidrocarburos_desest", hc.TOTALES, path)
            elif name == "ventas_combustibles":
                # gasoil/nafta/glp ajustadas directo + total_automotor derivado (indirecto).
                path = out / "ventas_combustibles_d11.csv"
                n = export_wide(conn, "etl_ventas_combustibles_desest",
                                ["gasoil", "nafta", "glp", "total_automotor"], path)
            elif name == "escrituras_caba":
                # Solo `compraventa` se desestacionaliza -> la vista _desest tiene una serie.
                path = out / "escrituras_caba_d11.csv"
                n = export_simple(conn, "etl_escrituras_caba_desest", path)
            elif name == "demanda_energia":
                # Solo no_residencial se desestacionaliza -> la vista _desest tiene una serie.
                path = out / "demanda_energia_d11.csv"
                n = export_simple(conn, "etl_demanda_energia_desest", path)
            else:  # cemento
                path = out / "cemento_d11.csv"
                n = export_simple(conn, "etl_cemento_despacho_desest", path)
            archivos += 1
            total += n
            extra = "  (vacio: corriste la desest?)" if n == 0 else ""
            print(f"  {name}  filas={n}  -> {path}{extra}")
    finally:
        conn.close()
    print(f"resumen [export]  archivos={archivos}  filas={total}")


if __name__ == "__main__":
    main()
