"""ETL incremental de faena avícola (MAGyP).

Baja el último Excel de "Indicadores de Oferta y Demanda" (scrapeando la página), parsea la
faena mensual (col Faena SENASA, todos los años) y snapshotea la serie con estado='definitivo':
es la cifra oficial de MAGyP/SENASA. El xlsx re-publica toda la serie 2016→, así que
insert_if_changed absorbe las revisiones de los últimos meses y se pone al día solo. Al final,
sólo si hubo datos nuevos o actualizados, desestacionaliza (X-13).

**Fallback en PDF**: el xlsx se actualiza más tarde que el PDF `Faena Avícola <año>.pdf` de la
misma página (jul-2026: el PDF ya traía junio y el xlsx llegaba hasta mayo). Después de cargar
el xlsx se miran los meses del PDF **posteriores al último del xlsx** y se insertan con
estado='provisorio', porque el PDF viene redondeado a la unidad (67.120) contra los 3 decimales
del xlsx (67119.556). Cuando el xlsx publique ese mes entra como 'definitivo' y la vista
`etl_aves_actual` lo prioriza sola (ordena definitivo > provisorio > histórico): no hay que
borrar nada. El PDF nunca pisa un mes que el xlsx ya tenga.

El histórico profundo (1981→) se carga aparte del Excel de referencia:
`python -m etl aves load-history`.

Flags:
  --force            insertar snapshot aunque no haya cambiado
  --no-desest        saltear la desestacionalización X-13
  --no-pdf-fallback  no completar los meses faltantes con el PDF
  --x13-out DIR      guardar la salida completa de X-13 en DIR
"""
from __future__ import annotations

import argparse

import urllib3

from etl.core import db, desest_params, report, seasonal
from . import config, source


def _completar_con_pdf(conn, rep, *, ultimo_xlsx, force: bool) -> None:
    """Inserta como 'provisorio' los meses del PDF posteriores al último mes del xlsx.

    Sólo completa hacia adelante (`> ultimo_xlsx`), nunca rellena huecos viejos: el PDF cubre
    dos años y trae valores redondeados, así que no es fuente para backfill.

    Si el PDF falla NO se marca la corrida como fallida: la fuente primaria (el xlsx) ya trajo
    su dato y esto es un extra. Marcarlo como falla mandaría mail cada día por un ETL que en
    realidad cumplió. Queda como AVISO en el log y la corrida siguiente reintenta.
    """
    try:
        res = source.get_latest_pdf()
    except Exception as e:
        rep.info(f"AVISO fallback PDF no disponible: {e}")
        return
    if not res or not res[0]:
        rep.info("AVISO fallback PDF: no se encontró el PDF de faena o no se parseó nada")
        return
    pdf, pdf_url = res
    faltantes = sorted(f for f in pdf if f > ultimo_xlsx)
    if not faltantes:
        rep.info(f"fallback PDF: sin meses nuevos (el xlsx ya llega a {ultimo_xlsx:%Y-%m})")
        return
    rep.info(f"fallback PDF: {pdf_url}")
    for fecha in faltantes:
        status = db.insert_if_changed(
            conn, table=config.TABLE, key_cols=config.KEY_COLS,
            key_vals=[config.MAIN_SERIE, fecha], value_cols=config.VALUE_COLS,
            row={"valor": float(pdf[fecha])},
            estado="provisorio", fuente=pdf_url, force=force,
        )
        rep.item(fecha, status, valor=pdf[fecha], estado="provisorio")


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="etl aves",
                                 description="ETL faena avícola (MAGyP)")
    ap.add_argument("--force", action="store_true", help="insertar aunque no cambie")
    ap.add_argument("--no-desest", action="store_true",
                    help="saltear la desestacionalización X-13")
    ap.add_argument("--no-pdf-fallback", action="store_true",
                    help="no completar los meses faltantes del xlsx con el PDF de faena")
    ap.add_argument("--x13-out", metavar="DIR",
                    help="guardar la salida de X-13 (html/factores/diagnósticos) en DIR")
    args = ap.parse_args(argv)
    urllib3.disable_warnings()  # cert de magyp.gob.ar (verify=False)

    rep = report.Report("aves", "run")
    conn = db.get_conn()
    try:
        try:
            res = source.get_latest()
        except Exception as e:
            rep.error(f"bajando/parseando: {e}")
            rep.summary()
            return
        if not res or not res[0]:
            rep.info("no se encontró el xlsx de indicadores o no se parseó ninguna fila")
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
            rep.tally(status)  # 125+ meses: se cuentan, no se imprime línea por mes

        if not args.no_pdf_fallback:
            _completar_con_pdf(conn, rep, ultimo_xlsx=max(data), force=args.force)
        rep.summary()

        if args.no_desest:
            pass
        elif not rep.changed:
            print("sin datos nuevos: no se desestacionaliza")
        else:
            seasonal.run_desest(conn, "aves",
                                desest_params.build_jobs("aves", keep_dir=args.x13_out))
    finally:
        conn.close()


if __name__ == "__main__":
    main()
