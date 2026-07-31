"""ETL incremental de molienda de oleaginosas (provisorios desde el HTML).

El HTML trae todos los meses publicados; por default se procesan los que están desde el
último mes en la base hacia adelante (re-chequeando los últimos por revisiones), así nunca
se saltea el último publicado. Hace insert-if-changed con estado='provisorio' y al final,
sólo si hubo datos nuevos o actualizados, corre la desestacionalización X-13 (salvo --no-desest).

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

from etl.core import db, desest_params, report, seasonal, window
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
    # El reporte se abre antes de bajar para que un fallo de la fuente salga por el mismo camino
    # que en el resto de los datasets (rep.error -> registro de fallas -> exit != 0) y no como un
    # traceback pelado. Era el único que reportaba la falla por excepción sin capturar.
    rep = report.Report("granos", "run")
    try:
        parsed = source.parse_molienda(source.fetch_html())
    except Exception as e:
        rep.error(f"bajando/parseando: {e}")
        rep.summary()
        return
    # Piso histórico: X-13 no puede con la serie desde 1965 (ver config.START_DATE).
    parsed = {d: r for d, r in parsed.items() if d >= config.START_DATE}
    if not parsed:
        rep.error("no se parseó ningún mes del HTML")
        rep.summary()
        return

    conn = db.get_conn()
    try:
        dates = target_dates(conn, parsed, args.month, args.months_back)
        rep.info(f"fuente: MAGyP HTML ({len(parsed)} meses {min(parsed):%Y-%m}.."
                 f"{max(parsed):%Y-%m}) | a procesar: {len(dates)}")
        for d in dates:
            row = parsed[d]
            for serie in config.SERIES:
                col = config.SERIE_COL.get(serie, serie)
                status = db.insert_if_changed(
                    conn, table=config.TABLE, key_cols=config.KEY_COLS,
                    key_vals=[serie, d], value_cols=config.VALUE_COLS,
                    row={"valor": row[col]}, estado=ESTADO,
                    fuente=source.PAGE_URL, force=args.force,
                )
                rep.item(f"{d:%Y-%m} {serie:9}", status, valor=row[col])
        rep.summary()

        if args.no_desest:
            pass
        elif not rep.changed:
            print("sin datos nuevos: no se desestacionaliza")
        else:
            seasonal.run_desest(conn, "granos",
                                desest_params.build_jobs("granos", keep_dir=args.x13_out))
    finally:
        conn.close()


if __name__ == "__main__":
    main()
