"""ETL incremental de faena avícola (MAGyP).

Baja el último Excel de "Indicadores de Oferta y Demanda" (scrapeando la página), parsea la
faena mensual (col Faena SENASA, todos los años) y snapshotea la serie con estado='definitivo':
es la cifra oficial de MAGyP/SENASA. El xlsx re-publica toda la serie 2016→, así que
insert_if_changed absorbe las revisiones de los últimos meses y se pone al día solo. Al final,
sólo si hubo datos nuevos o actualizados, desestacionaliza (X-13).

**Fallback en PDF**: el xlsx se actualiza más tarde que el PDF `Faena Avícola <año>.pdf` de la
misma página (jul-2026: el PDF ya traía junio y el xlsx llegaba hasta mayo). Se insertan con
estado='provisorio' los meses del PDF posteriores a lo que ya tenemos, porque el PDF viene
redondeado a la unidad (67.120) contra los 3 decimales del xlsx (67119.556). Cuando el xlsx
publique ese mes entra como 'definitivo' y la vista `etl_aves_actual` lo prioriza sola (ordena
definitivo > provisorio > histórico): no hay que borrar nada. El PDF nunca pisa un mes que el
xlsx ya tenga, ni rellena huecos viejos (sólo cubre dos años, y redondeado).

Cubre **dos** escenarios distintos:
  - xlsx desactualizado -> la referencia es el último mes del xlsx.
  - xlsx caído (404, layout roto) -> la corrida NO se corta: la referencia pasa a ser el último
    mes observado en la base y el PDF salva el mes igual. La falla del xlsx se registra igual y
    el proceso sale != 0: que la fuente primaria esté rota es noticia aunque haya rescate.

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


def _completar_con_pdf(conn, rep, *, ultimo, force: bool) -> None:
    """Inserta como 'provisorio' los meses del PDF posteriores a `ultimo`.

    `ultimo` es hasta dónde ya tenemos cubierta la serie: el último mes del xlsx si anduvo, o
    el último mes observado en la base si el xlsx falló. Sólo completa hacia adelante, nunca
    rellena huecos viejos: el PDF cubre dos años y trae valores redondeados, así que no es
    fuente para backfill.

    Si el PDF falla NO se marca la corrida como fallida: es una fuente de respaldo, y cuando el
    xlsx anduvo la corrida ya cumplió. Si el xlsx TAMBIÉN falló, la falla ya quedó registrada
    por su propio camino, así que el exit code sale != 0 igual. Queda como AVISO en el log y la
    corrida siguiente reintenta.
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
    # `ultimo=None` sólo con la tabla vacía (base nueva sin load-history): ahí entra todo el PDF.
    faltantes = sorted(f for f in pdf if ultimo is None or f > ultimo)
    if not faltantes:
        rep.info(f"fallback PDF: sin meses nuevos (ya cubiertos hasta {ultimo:%Y-%m})")
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
        # El xlsx es la fuente primaria, pero que falle NO corta la corrida: el PDF puede
        # salvar el mes igual (son dos archivos distintos y puede romperse uno solo, p.ej. si
        # cambia el link del xlsx o su layout). La falla queda registrada igual: que la fuente
        # primaria este rota es noticia aunque el fallback rescate el dato.
        data = url = None
        try:
            res = source.get_latest()
            if res and res[0]:
                data, url = res
            else:
                rep.error("no se encontró el xlsx de indicadores o no se parseó ninguna fila")
        except Exception as e:
            rep.error(f"bajando/parseando el xlsx: {e}")

        if data:
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
            # Referencia de "hasta donde ya tenemos": el xlsx recien cargado si anduvo, y si no
            # el ultimo mes observado en la base. Sin esto, con el xlsx caido el PDF reinsertaria
            # sus 2 años completos como provisorio todos los dias (insert_if_changed compara
            # contra el ultimo snapshot del MISMO estado, y no hay provisorios previos).
            ultimo = max(data) if data else db.last_date(
                conn, table=config.TABLE,
                where="serie = %s and estado is distinct from 'desestacionalizado'",
                where_params=(config.MAIN_SERIE,))
            _completar_con_pdf(conn, rep, ultimo=ultimo, force=args.force)
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
