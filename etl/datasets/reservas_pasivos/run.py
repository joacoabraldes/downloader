"""ETL incremental de reservas internacionales y principales pasivos del BCRA (diario).

Baja `diar_bas.xls` (URL fija), parsea las 37 series diarias y snapshotea con estado='definitivo'.
Modelo append-only: insert_if_changed absorbe las revisiones del BCRA sin duplicar.

El archivo trae TODO el histórico (1996→hoy). Para no re-chequear ~277k celdas contra la base en
cada corrida del cron, por defecto sólo se procesan los días recientes (ventana de relectura, para
captar revisiones). La primera corrida (tabla vacía) o `--full` procesan todo el histórico.

Sin desestacionalización: es un stock/nivel diario, no una serie mensual de flujo.

Flags:
  --full      procesar todo el histórico del archivo (no sólo la ventana reciente).
  --force     insertar snapshot aunque no haya cambiado.
  --lookback N  días hacia atrás a re-procesar en modo incremental (default: 15).
"""
from __future__ import annotations

import argparse
import datetime as dt

import urllib3

from etl.core import db, report
from . import config, source

LOOKBACK_DEFAULT = 15  # días re-procesados en incremental para captar revisiones del BCRA


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="etl reservas_pasivos",
                                 description="ETL reservas internacionales y pasivos del BCRA (diario)")
    ap.add_argument("--full", action="store_true",
                    help="procesar todo el histórico (no sólo la ventana reciente)")
    ap.add_argument("--force", action="store_true", help="insertar aunque no cambie")
    ap.add_argument("--lookback", type=int, default=LOOKBACK_DEFAULT, metavar="N",
                    help=f"días a re-procesar en incremental (default: {LOOKBACK_DEFAULT})")
    args = ap.parse_args(argv)
    urllib3.disable_warnings()  # cert de bcra.gob.ar (verify=False)

    rep = report.Report("reservas_pasivos", "run")
    conn = db.get_conn()
    try:
        try:
            res = source.get_latest()
        except Exception as e:
            rep.error(f"bajando/parseando: {e}")
            rep.summary()
            return
        if not res or not any(res[0].values()):
            rep.error("no se pudo bajar/parsear el diar_bas.xls del BCRA")
            rep.summary()
            return
        data, url = res

        last = db.last_date(conn, table=config.TABLE)
        empty = last is None
        cutoff = None
        if not empty and not args.full:
            cutoff = last - dt.timedelta(days=args.lookback)

        fechas_todas = sorted({f for serie in data.values() for f in serie})
        if not fechas_todas:
            rep.info("el archivo no trae fechas")
            rep.summary()
            return

        if empty:
            # Primera carga (tabla vacía): backfill masivo en una transacción. No hay nada contra
            # qué deduplicar, así que se evita el SELECT+COMMIT por fila (~200k filas, inviable).
            rep.info(f"fuente: {url} | series: {len(data)} | "
                     f"fechas: {fechas_todas[0]}..{fechas_todas[-1]} | modo: backfill masivo")
            rows = [
                (cd, fecha, float(valor), "definitivo", url)
                for cd in sorted(data, key=int)
                for fecha, valor in sorted(data[cd].items())
                if valor is not None
            ]
            n = db.bulk_insert(conn, table=config.TABLE,
                               cols=["serie", "date", "valor", "estado", "fuente"], rows=rows)
            for _ in range(n):
                rep.tally("nuevo")
            rep.summary()
            return

        # Incremental: sólo la ventana reciente (para captar revisiones del BCRA), con dedup.
        modo = "full" if args.full else f"incremental desde {cutoff}"
        rep.info(f"fuente: {url} | series: {len(data)} | "
                 f"fechas: {fechas_todas[0]}..{fechas_todas[-1]} | modo: {modo}")
        for cd in sorted(data, key=int):
            for fecha in sorted(data[cd]):
                if cutoff is not None and fecha < cutoff:
                    continue
                valor = data[cd][fecha]
                status = db.insert_if_changed(
                    conn, table=config.TABLE, key_cols=config.KEY_COLS,
                    key_vals=[cd, fecha], value_cols=config.VALUE_COLS,
                    row={"valor": None if valor is None else float(valor)},
                    estado="definitivo", fuente=url, force=args.force,
                )
                rep.tally(status)
        rep.summary()
    finally:
        conn.close()


if __name__ == "__main__":
    main()
