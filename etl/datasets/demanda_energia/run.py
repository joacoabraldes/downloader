"""ETL incremental de demanda energética del MEM (CAMMESA).

Baja el Excel "Demanda Mensual" de CAMMESA, parsea todas las series del cuadro (2023→,
re-publicado entero cada mes) y snapshotea cada una con estado='definitivo'. insert_if_changed
absorbe las revisiones de los últimos meses. Al final, sólo si hubo datos nuevos o actualizados,
desestacionaliza `no_residencial` (X-13).

El histórico profundo de no_residencial (2005→2022) se carga aparte del Excel de referencia:
`python -m etl demanda_energia load-history`.

Flags:
  --force        insertar snapshot aunque no haya cambiado
  --no-desest    saltear la desestacionalización X-13
  --x13-out DIR  guardar la salida completa de X-13 en DIR
"""
from __future__ import annotations

import argparse

from etl.core import db, desest_params, report, seasonal
from . import config, source


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="etl demanda_energia",
                                 description="ETL demanda energética del MEM (CAMMESA)")
    ap.add_argument("--force", action="store_true", help="insertar aunque no cambie")
    ap.add_argument("--no-desest", action="store_true",
                    help="saltear la desestacionalización X-13")
    ap.add_argument("--x13-out", metavar="DIR",
                    help="guardar la salida de X-13 (html/factores/diagnósticos) en DIR")
    args = ap.parse_args(argv)

    rep = report.Report("demanda_energia", "run")
    conn = db.get_conn()
    try:
        try:
            res = source.get_latest()
        except Exception as e:
            rep.info(f"ERROR bajando/parseando: {e}")
            rep.summary()
            return
        if not res or not res[0]:
            rep.info("no se pudo bajar/parsear el Excel de CAMMESA")
            rep.summary()
            return
        data, url = res
        meses = sorted({d for serie in data.values() for d in serie})
        rep.info(f"fuente: {url} | series: {len(data)} | meses: "
                 f"{meses[0]:%Y-%m}..{meses[-1]:%Y-%m}")
        for serie in sorted(data):
            for fecha in sorted(data[serie]):
                valor = data[serie][fecha]
                status = db.insert_if_changed(
                    conn, table=config.TABLE, key_cols=config.KEY_COLS,
                    key_vals=[serie, fecha], value_cols=config.VALUE_COLS,
                    row={"valor": None if valor is None else float(valor)},
                    estado="definitivo", fuente=url, force=args.force,
                )
                rep.tally(status)
        rep.summary()

        if args.no_desest:
            pass
        elif not rep.changed:
            print("sin datos nuevos: no se desestacionaliza")
        else:
            seasonal.run_desest(conn, "demanda_energia",
                                desest_params.build_jobs("demanda_energia", keep_dir=args.x13_out))
    finally:
        conn.close()


if __name__ == "__main__":
    main()
