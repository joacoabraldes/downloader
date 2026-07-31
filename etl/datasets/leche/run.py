"""ETL incremental de producción de leche (MAGyP, Dir. Nacional de Lechería).

Baja el Excel `PPV021_PPV022.xlsx` (URL fija), parsea la producción mensual (litros) y
snapshotea la serie con estado='definitivo'. El Excel re-publica toda la serie 2015→, así que
insert_if_changed absorbe las revisiones de los últimos meses y se pone al día solo. Al final,
sólo si hubo datos nuevos o actualizados, desestacionaliza (X-13).

El histórico también se puede cargar del Excel de referencia: `python -m etl leche load-history`.

Flags:
  --force        insertar snapshot aunque no haya cambiado
  --no-desest    saltear la desestacionalización X-13
  --x13-out DIR  guardar la salida completa de X-13 en DIR
"""
from __future__ import annotations

import argparse

import urllib3

from etl.core import db, desest_params, report, seasonal
from . import config, source


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="etl leche",
                                 description="ETL producción de leche (MAGyP)")
    ap.add_argument("--force", action="store_true", help="insertar aunque no cambie")
    ap.add_argument("--no-desest", action="store_true",
                    help="saltear la desestacionalización X-13")
    ap.add_argument("--x13-out", metavar="DIR",
                    help="guardar la salida de X-13 (html/factores/diagnósticos) en DIR")
    args = ap.parse_args(argv)
    urllib3.disable_warnings()  # cert de magyp.gob.ar (verify=False)

    rep = report.Report("leche", "run")
    conn = db.get_conn()
    try:
        try:
            res = source.get_latest()
        except Exception as e:
            rep.error(f"bajando/parseando: {e}")
            rep.summary()
            return
        if not res or not res[0]:
            rep.error("no se pudo bajar/parsear el Excel PPV de producción")
            rep.summary()
            return
        data, url = res
        rep.info(f"fuente: {url} | meses: {min(data):%Y-%m}..{max(data):%Y-%m}")
        for fecha in sorted(data):
            valor = data[fecha]
            status = db.insert_if_changed(
                conn, table=config.TABLE, key_cols=config.KEY_COLS,
                key_vals=[config.MAIN_SERIE, fecha], value_cols=config.VALUE_COLS,
                row={"valor": None if valor is None else float(valor)},
                estado="definitivo", fuente=url, force=args.force,
            )
            rep.tally(status)  # 130+ meses: se cuentan, no se imprime línea por mes
        rep.summary()

        if args.no_desest:
            pass
        elif not rep.changed:
            print("sin datos nuevos: no se desestacionaliza")
        else:
            seasonal.run_desest(conn, "leche",
                                desest_params.build_jobs("leche", keep_dir=args.x13_out))
    finally:
        conn.close()


if __name__ == "__main__":
    main()
