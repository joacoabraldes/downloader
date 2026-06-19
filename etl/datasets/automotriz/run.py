"""ETL incremental de la industria automotriz (ADEFA -> Supabase).

Por cada mes objetivo baja el PDF de ADEFA y snapshotea las 3 series (produccion,
ventas, expo) con estado='provisorio'. Al final desestacionaliza cada serie por
separado (X-13).

Ventana de meses: por default se pone al día desde el último mes que hay en la base
(re-chequeando los últimos meses por revisiones) hasta hoy, así nunca se "saltea" el
último mes publicado. `--month` y `--months-back` siguen como override.

Flags:
  --month YYYY-MM     procesar solo ese mes
  --months-back N     últimos N meses a revisar (override de la ventana auto)
  --force             insertar snapshot aunque no haya cambiado
  --no-fetch          saltear la descarga del PDF (solo desestacionalizar el histórico)
  --no-desest         saltear la desestacionalización X-13
  --x13-out DIR       guardar la salida completa de X-13 en DIR
"""
from __future__ import annotations

import argparse

import urllib3

from etl.core import db, report, seasonal, window
from . import config, source


def process_month(conn, rep, fecha, *, force: bool) -> None:
    """Baja el PDF del mes y snapshotea las 3 series, reportando cada una."""
    try:
        data = source.get_month(fecha.year, fecha.month)
        fuente = source.pdf_url(fecha.year, fecha.month)
    except Exception as e:  # red caída, PDF inesperado, etc.
        rep.note(fecha, f"ERROR {e}", status="saltado")
        return
    if not data:
        rep.note(fecha, "no publicado")
        return
    for serie in config.SERIES:
        valor = data.get(serie)
        status = db.insert_if_changed(
            conn, table=config.TABLE, key_cols=config.KEY_COLS,
            key_vals=[serie, fecha], value_cols=config.VALUE_COLS,
            row={"valor": None if valor is None else float(valor)},
            estado="provisorio", fuente=fuente, force=force,
        )
        rep.item(f"{fecha:%Y-%m} {serie:11}", status, valor=valor)


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="etl automotriz",
                                 description="ETL automotriz ADEFA")
    ap.add_argument("--month", help="mes puntual YYYY-MM (ignora --months-back)")
    ap.add_argument("--months-back", type=int,
                    help="últimos N meses a revisar (override de la ventana auto)")
    ap.add_argument("--force", action="store_true", help="insertar aunque no cambie")
    ap.add_argument("--no-fetch", action="store_true",
                    help="saltear la descarga del PDF (solo desestacionalizar)")
    ap.add_argument("--no-desest", action="store_true",
                    help="saltear la desestacionalización X-13")
    ap.add_argument("--x13-out", metavar="DIR",
                    help="guardar la salida de X-13 (html/factores/diagnósticos) en DIR")
    args = ap.parse_args(argv)
    urllib3.disable_warnings()  # cert de ADEFA (verify=False)

    conn = db.get_conn()
    try:
        if not args.no_fetch:
            months = window.target_months(conn, table=config.TABLE, month=args.month,
                                          months_back=args.months_back)
            rep = report.Report("automotriz", "run")
            rep.info(f"fuente: ADEFA PDF | meses: {months[0]:%Y-%m}..{months[-1]:%Y-%m}")
            for fecha in months:
                process_month(conn, rep, fecha, force=args.force)
            rep.summary()

        if not args.no_desest:
            jobs = [(serie, dict(
                        table=config.TABLE, source_view=config.ACTUAL_VIEW,
                        conflict_cols=("serie", "date"), extra_cols={"serie": serie},
                        where="serie = %s", where_params=(serie,), keep_dir=args.x13_out))
                    for serie in config.SERIES]
            seasonal.run_desest(conn, "automotriz", jobs)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
