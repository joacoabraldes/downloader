"""ETL incremental del despacho de cemento (AFCP -> Supabase).

Para cada mes objetivo intenta traer los valores provisorio y definitivo de AFCP,
insertando un snapshot sólo si es nuevo o cambió (append-only con dedup). Al terminar
corre la desestacionalización (X-13).

Ventana de meses: por default se pone al día desde el último mes que hay en la base hasta
hoy (re-chequeando los últimos meses por revisiones). `--month` y `--months-back` siguen
como override.

Ejemplos:
    python -m etl cemento                 # ponerse al día + desestacionalización
    python -m etl cemento --months-back 6
    python -m etl cemento --month 2026-04 # un mes puntual
    python -m etl cemento --force         # inserta aunque el valor no haya cambiado
    python -m etl cemento --no-desest     # saltea la desestacionalización
"""
from __future__ import annotations

import argparse

from etl.core import db, report, seasonal, window
from . import config, source


def _row_from_fields(fields: dict) -> dict:
    """Mapea el dict del parser a las columnas de la tabla (despacho_nacional->valor)."""
    return {
        "valor": fields.get("despacho_nacional"),
        "exportacion": fields.get("exportacion"),
        "consumo_despacho_nacional": fields.get("consumo_despacho_nacional"),
        "importaciones_propias": fields.get("importaciones_propias"),
    }


def process_month(conn, rep, fecha, *, force: bool) -> None:
    """Trae provisorio y definitivo del mes y los snapshotea si corresponde."""
    # Si el mes ya tiene definitivo, el dato es final: no se vuelve a bajar (salvo --force).
    if not force and db.has_estado(conn, table=config.TABLE, key_cols=config.KEY_COLS,
                                   key_vals=[fecha], estado="definitivo"):
        rep.note(f"{fecha:%Y-%m} definitivo ", "ya cerrado", status="sin_cambios")
        return
    for estado, getter in (("provisorio", source.get_provisorio),
                           ("definitivo", source.get_definitivo)):
        label = f"{fecha:%Y-%m} {estado:10}"
        try:
            fields, url = getter(fecha.year, fecha.month)
        except Exception as e:  # red caída, HTML inesperado, etc.
            rep.note(label, f"ERROR {e}", status="saltado")
            continue
        if fields is None:
            rep.note(label, "no publicado")
            continue
        row = _row_from_fields(fields)
        status = db.insert_if_changed(
            conn, table=config.TABLE, key_cols=config.KEY_COLS, key_vals=[fecha],
            value_cols=config.VALUE_COLS, row=row, estado=estado, fuente=url, force=force,
        )
        rep.item(label, status, valor=row["valor"])


def main(argv=None):
    ap = argparse.ArgumentParser(prog="etl cemento",
                                 description="ETL despacho de cemento AFCP")
    ap.add_argument("--months-back", type=int,
                    help="cantidad de meses hacia atrás a revisar (override de la ventana auto)")
    ap.add_argument("--month", help="mes puntual YYYY-MM (ignora --months-back)")
    ap.add_argument("--force", action="store_true",
                    help="inserta snapshot aunque el valor no haya cambiado")
    ap.add_argument("--no-desest", action="store_true",
                    help="saltea la desestacionalización (X-13) al final")
    ap.add_argument("--x13-out", metavar="DIR",
                    help="guardar la salida de X-13 (html/factores/diagnósticos) en DIR")
    args = ap.parse_args(argv)

    conn = db.get_conn()
    try:
        months = window.target_months(conn, table=config.TABLE, month=args.month,
                                      months_back=args.months_back)
        rep = report.Report("cemento", "run")
        rep.info(f"fuente: AFCP HTML | meses: {months[0]:%Y-%m}..{months[-1]:%Y-%m}")
        for fecha in months:
            process_month(conn, rep, fecha, force=args.force)
        rep.summary()

        if not args.no_desest:
            seasonal.run_desest(conn, "cemento", [
                (config.TABLE, dict(table=config.TABLE, source_view=config.ACTUAL_VIEW,
                                    keep_dir=args.x13_out))])
    finally:
        conn.close()


if __name__ == "__main__":
    main()
