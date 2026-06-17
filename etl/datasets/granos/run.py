"""ETL incremental de molienda de oleaginosas (provisorios desde el HTML).

El HTML trae todos los meses publicados; por default se procesan los que están desde el
último mes en la base hacia adelante (re-chequeando los últimos por revisiones), así nunca
se saltea el último publicado. Hace insert-if-changed con estado='provisorio' y al final
corre la desestacionalización X-13 (salvo --no-desest).

Flags:
  --month YYYY-MM     procesar solo ese mes
  --months-back N     procesar los últimos N meses publicados (override de la ventana auto)
  --force             insertar snapshot aunque no haya cambiado
  --no-desest         saltear la etapa de desestacionalización
  --x13-out DIR       guardar la salida completa de X-13 en DIR
"""
from __future__ import annotations

import argparse
import datetime as dt
import sys

import urllib3

from etl.core import db, report, seasonal, window
from . import config, source

ESTADO = "provisorio"


def parse_month(s: str) -> dt.date:
    return dt.datetime.strptime(s, "%Y-%m").date().replace(day=1)


def target_dates(conn, parsed: dict[dt.date, dict], month: str | None,
                 months_back: int | None) -> list[dt.date]:
    """Lista de meses a procesar, dentro de los que trae el HTML."""
    available = sorted(parsed)
    if month:
        d = parse_month(month)
        return [d] if d in parsed else _missing(d, available)
    if months_back is not None:
        return available[-months_back:] if months_back > 0 else available
    # default: ponerse al día desde el último mes en la base (acotado por el HTML).
    start = window.catch_up_start(conn, table=config.TABLE)
    return [d for d in available if d >= start]


def _missing(d: dt.date, available: list[dt.date]) -> list[dt.date]:
    print(f"[warn] {d:%Y-%m} no está publicado en el HTML "
          f"(rango {available[0]:%Y-%m}..{available[-1]:%Y-%m}).", file=sys.stderr)
    return []


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="etl granos",
                                 description="ETL incremental molienda oleaginosas.")
    ap.add_argument("--month", help="procesar solo este mes (YYYY-MM)")
    ap.add_argument("--months-back", type=int,
                    help="últimos N meses publicados a revisar (override de la ventana auto)")
    ap.add_argument("--force", action="store_true", help="insertar aunque no cambie")
    ap.add_argument("--no-desest", action="store_true",
                    help="saltear desestacionalización X-13")
    ap.add_argument("--x13-out", metavar="DIR",
                    help="guardar la salida de X-13 (html/factores/diagnósticos) en DIR")
    args = ap.parse_args(argv)

    urllib3.disable_warnings()
    html = source.fetch_html()
    parsed = source.parse_molienda(html)
    if not parsed:
        print("No se parseó ningún mes del HTML.", file=sys.stderr)
        sys.exit(1)

    conn = db.get_conn()
    try:
        dates = target_dates(conn, parsed, args.month, args.months_back)
        rep = report.Report("granos", "run")
        rep.info(f"fuente: MAGyP HTML ({len(parsed)} meses {min(parsed):%Y-%m}.."
                 f"{max(parsed):%Y-%m}) | a procesar: {len(dates)}")
        for d in dates:
            row = parsed[d]
            status = db.insert_if_changed(
                conn, table=config.TABLE, key_cols=config.KEY_COLS, key_vals=[d],
                value_cols=config.VALUE_COLS, row=row, estado=ESTADO,
                fuente=source.PAGE_URL, force=args.force,
            )
            rep.item(d, status, valor=row["valor"])
        rep.summary()

        if not args.no_desest:
            try:
                seasonal.deseasonalize(conn, table=config.TABLE,
                                       source_view=config.ACTUAL_VIEW,
                                       keep_dir=args.x13_out)
            except Exception as e:  # degradación elegante: el ETL no se rompe
                print(f"[desest] saltado: {e}", file=sys.stderr)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
