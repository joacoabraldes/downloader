"""ETL incremental del Índice de Confianza del Consumidor (UTDT -> Postgres).

Baja la planilla de UTDT (resolviendo el link en cada corrida, ver `etl.core.utdt`), parsea
las 7 series y snapshotea cada (serie, mes) con estado='definitivo'. `insert_if_changed`
absorbe las revisiones que UTDT hace sobre meses ya publicados.

No hay `load_history`: la planilla ES el histórico completo (1998-07 →), así que la primera
corrida carga todo y las siguientes sólo agregan el mes nuevo.

El ICC no se desestacionaliza (ver la nota en etl/series_desest.toml).

Flags:
  --force        insertar snapshot aunque no haya cambiado
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
    ap = argparse.ArgumentParser(prog="etl icc",
                                 description="ETL del ICC de UTDT")
    ap.add_argument("--force", action="store_true", help="insertar aunque no cambie")
    ap.add_argument("--desde", metavar="YYYY-MM", type=_mes,
                    help="cargar sólo desde ese mes (default: toda la planilla)")
    args = ap.parse_args(argv)

    rep = report.Report("icc", "run")
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
        rep.info(f"fuente: {url} | series: {len(config.SERIES)} | "
                 f"meses: {meses[0]:%Y-%m}..{meses[-1]:%Y-%m}")
        for fecha in meses:
            for serie in config.SERIES:
                valor = datos[fecha].get(serie)
                if valor is None:
                    continue  # esa serie no cubre ese mes (las aperturas arrancan en 2001-03)
                rep.tally(db.insert_if_changed(
                    conn, table=config.TABLE, key_cols=config.KEY_COLS,
                    key_vals=[serie, fecha], value_cols=config.VALUE_COLS,
                    row={"valor": float(valor)},
                    estado="definitivo", fuente=url, force=args.force,
                ))
        rep.summary()
    finally:
        conn.close()


if __name__ == "__main__":
    main()
