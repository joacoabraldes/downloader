"""ETL incremental del Índice de Confianza en el Gobierno (UTDT -> Postgres).

Baja la planilla de UTDT (resolviendo el link en cada corrida, ver `etl.core.utdt`), parsea
la serie completa y snapshotea cada mes con estado='definitivo'. `insert_if_changed` absorbe
las revisiones que UTDT hace sobre meses ya publicados.

No hay `load_history`: la planilla ES el histórico completo (2001-11 →), así que la primera
corrida carga todo y las siguientes sólo agregan el mes nuevo.

El ICG no se desestacionaliza (ver la nota en etl/series_desest.toml).

Flags:
  --force          insertar snapshot aunque no haya cambiado
  --desde YYYY-MM  ignorar los meses anteriores (por defecto se carga toda la planilla)
"""
from __future__ import annotations

import argparse
import datetime as dt

from etl.core import db, report
from . import config, source


def _mes(texto: str) -> dt.date:
    y, m = map(int, texto.split("-"))
    return dt.date(y, m, 1)


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="etl icg",
                                 description="ETL del ICG de UTDT")
    ap.add_argument("--force", action="store_true", help="insertar aunque no cambie")
    ap.add_argument("--desde", metavar="YYYY-MM", type=_mes,
                    help="cargar sólo desde ese mes (default: toda la planilla)")
    args = ap.parse_args(argv)

    rep = report.Report("icg", "run")
    conn = db.get_conn()
    try:
        try:
            datos, url = source.get_serie()
        except Exception as e:
            rep.error(f"bajando/parseando: {e}")
            rep.summary()
            return
        meses = sorted(f for f in datos if args.desde is None or f >= args.desde)
        if not meses:
            rep.error("la planilla no trajo ningún mes")
            rep.summary()
            return
        rep.info(f"fuente: {url} | meses: {meses[0]:%Y-%m}..{meses[-1]:%Y-%m}")
        for fecha in meses:
            rep.tally(db.insert_if_changed(
                conn, table=config.TABLE, key_cols=config.KEY_COLS,
                key_vals=[config.MAIN_SERIE, fecha], value_cols=config.VALUE_COLS,
                row={"valor": float(datos[fecha])},
                estado="definitivo", fuente=url, force=args.force,
            ))
        rep.summary()
    finally:
        conn.close()


if __name__ == "__main__":
    main()
