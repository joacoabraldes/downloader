"""ETL incremental de patentamientos 4W (SIOMAA).

A diferencia de los otros datasets, SIOMAA sólo expone el ÚLTIMO informe gratuito: no se
puede pedir un mes arbitrario por URL. Así que el incremental es "bajar el último": se
descubre el informe más nuevo y, si ese mes no está en la base, se descarga (flujo de
verificación por email), se parsea la Tabla 1 y se snapshotean las series con
estado='definitivo'. Al final, sólo si hubo datos nuevos, desestacionaliza (X-13).

SIOMAA publica el dato **final** del mes (no un provisorio que después revise), por eso el
mensual va como 'definitivo' y no 'provisorio' como en automotriz/granos. El histórico del
backfill va como NULL; en la vista _actual, 'definitivo' tiene prioridad sobre ese NULL.

El histórico se carga aparte desde los PDFs ya bajados: `python -m etl patentamientos load-history --dir CARPETA`.

Flags:
  --force        insertar snapshot aunque no haya cambiado (y bajar aunque el mes ya esté)
  --no-desest    saltear la desestacionalización X-13
  --x13-out DIR  guardar la salida completa de X-13 en DIR
"""
from __future__ import annotations

import argparse
import datetime as dt

from etl.core import db, desest_params, report, seasonal
from . import config, source


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="etl patentamientos",
                                 description="ETL patentamientos 4W (SIOMAA)")
    ap.add_argument("--force", action="store_true",
                    help="insertar aunque no cambie (y bajar aunque el mes ya esté)")
    ap.add_argument("--no-desest", action="store_true",
                    help="saltear la desestacionalización X-13")
    ap.add_argument("--x13-out", metavar="DIR",
                    help="guardar la salida de X-13 (html/factores/diagnósticos) en DIR")
    args = ap.parse_args(argv)

    rep = report.Report("patentamientos", "run")
    conn = db.get_conn()
    try:
        latest = source.get_latest_report()
        if not latest:
            rep.info("SIOMAA no devolvió ningún informe 4W gratuito")
            rep.summary()
            return
        rep.info(f"fuente: SIOMAA | informe: {latest['name']}")

        # Si ya tenemos ese mes observado (definitivo del run o NULL del histórico), evitamos
        # la descarga: es cara (incluye esperar el email de verificación).
        year, month = latest["year"], latest["month"]
        if year and month and not args.force:
            key = [config.MAIN_SERIE, dt.date(year, month, 1)]
            ya = any(db.has_estado(conn, table=config.TABLE, key_cols=config.KEY_COLS,
                                   key_vals=key, estado=e) for e in ("definitivo", None))
            if ya:
                rep.note(dt.date(year, month, 1), "ya está en la base", status="sin_cambios")
                rep.summary()
                return

        try:
            pdf_bytes = source.download_pdf_bytes(latest["id"])
            year, month, data = source.parse_report(pdf_bytes)  # período autoritativo del PDF
        except Exception as e:
            rep.error(f"bajando/parseando: {e}")
            rep.summary()
            return
        if not year or not month or not data:
            rep.error("no se pudo determinar el mes o parsear la Tabla 1")
            rep.summary()
            return

        fecha = dt.date(year, month, 1)
        for serie in config.SERIES:
            valor = data.get(serie)
            status = db.insert_if_changed(
                conn, table=config.TABLE, key_cols=config.KEY_COLS,
                key_vals=[serie, fecha], value_cols=config.VALUE_COLS,
                row={"valor": None if valor is None else float(valor)},
                estado="definitivo", fuente=latest["name"], force=args.force,
            )
            rep.item(f"{fecha:%Y-%m} {serie:17}", status, valor=valor)
        rep.summary()

        if args.no_desest:
            pass
        elif not rep.changed:
            print("sin datos nuevos: no se desestacionaliza")
        else:
            seasonal.run_desest(conn, "patentamientos",
                                desest_params.build_jobs("patentamientos", keep_dir=args.x13_out))
    finally:
        conn.close()


if __name__ == "__main__":
    main()
