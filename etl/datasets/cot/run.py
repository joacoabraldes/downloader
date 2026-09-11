"""ETL incremental del Commitments of Traders (CFTC), por la API Socrata.

La CFTC publica los viernes 15:30 ET con el corte del martes anterior. La corrida trae las
últimas semanas de los cuatro orígenes (managed_money y non_commercial, cada uno en futures y
futures_options) con un request por origen.

Por qué re-lee varias semanas y no sólo la nueva: **la CFTC revisa reportes ya publicados**.
Con la tabla append-only y `insert_if_changed`, releer es barato (no inserta si no cambió) y es
lo único que hace entrar la revisión. Por defecto son 8 semanas hacia atrás.

Un origen que no trae ninguna semana nueva NO es falla: el viernes todavía no publicó, o la
corrida cae un lunes. Sí es falla que la API no conteste o que cambie el formato.

Flags:
  --semanas N    semanas hacia atrás a re-leer (default: 8)
  --desde FECHA  releer desde una fecha concreta (ignora --semanas)
  --force        insertar snapshot aunque no haya cambiado
"""
from __future__ import annotations

import argparse
import datetime as dt

from etl.core import db, report
from . import config, source

SEMANAS_DEFAULT = 8


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="etl cot",
                                 description="ETL semanal del Commitments of Traders (CFTC)")
    ap.add_argument("--semanas", type=int, default=SEMANAS_DEFAULT, metavar="N",
                    help=f"semanas hacia atrás a re-leer (default: {SEMANAS_DEFAULT})")
    ap.add_argument("--desde", type=dt.date.fromisoformat, metavar="AAAA-MM-DD",
                    help="releer desde esta fecha (ignora --semanas)")
    ap.add_argument("--force", action="store_true", help="insertar aunque no cambie")
    args = ap.parse_args(argv)

    rep = report.Report("cot", "run")
    conn = db.get_conn()
    try:
        desde = args.desde or dt.date.today() - dt.timedelta(weeks=args.semanas)
        rep.info(f"desde {desde} | origenes: {len(config.ORIGENES)}")

        total = 0
        for (categoria, tipo), o in config.ORIGENES.items():
            # La API no llega tan atrás como los zips: legacy arranca en 1998-01-06 y
            # disaggregated en 2006-06-13. Pedir antes no rompe, devuelve vacío, pero el piso
            # deja claro en el log que el tramo viejo es trabajo de `load-history`.
            piso = dt.date.fromisoformat(config.API_DESDE[categoria])
            desde_origen = max(desde, piso)
            try:
                filas = source.bajar_api(o["socrata"], categoria, tipo, o["layout"], desde_origen)
            except Exception as e:  # noqa: BLE001 - red/HTTP/formato: falla de la corrida
                rep.note(f"{categoria}/{tipo}", f"error bajando: {e}", failure=True)
                continue
            total += len(filas)
            rep.info(f"{categoria}/{tipo}: {len(filas)} filas desde {desde_origen}")
            for f in filas:
                rep.tally(db.insert_if_changed(
                    conn, table=config.TABLE, key_cols=config.KEY_COLS,
                    key_vals=[f["contrato"], f["categoria"], f["tipo"], f["date"]],
                    value_cols=config.VALUE_COLS,
                    row={c: f[c] for c in config.VALUE_COLS},
                    estado=config.ESTADO, fuente=f["fuente"], force=args.force,
                    extra={"codigo_cftc": f["codigo_cftc"], "unidad": f["unidad"]},
                ))
        # Los cuatro orígenes vacíos no es "todavía no publicaron": es la API caída o el
        # filtro roto. Un origen solo vacío sí puede ser normal.
        if total == 0:
            rep.error(f"ningún origen trajo filas desde {desde}")
        rep.summary()
    finally:
        conn.close()


if __name__ == "__main__":
    main()
