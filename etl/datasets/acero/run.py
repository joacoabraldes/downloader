"""ETL incremental de acero crudo (Cámara Argentina del Acero).

Baja el último PDF de "Cifras" (scrapeando la página, porque el nombre es inconsistente),
parsea las ~13 filas mensuales y snapshotea acero crudo con estado='definitivo': la cifra
publicada por la CAA es el número oficial del mes. Como cada PDF re-publica los últimos 13
meses, insert_if_changed absorbe las revisiones de la CAA (un valor corregido entra como
snapshot definitivo nuevo) y se pone al día solo. Al final, sólo si hubo datos nuevos o
actualizados, desestacionaliza (X-13).

El histórico profundo (desde 1993) se carga aparte del Excel de referencia:
`python -m etl acero load-history`.

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
    ap = argparse.ArgumentParser(prog="etl acero",
                                 description="ETL acero crudo (CAA)")
    ap.add_argument("--force", action="store_true", help="insertar aunque no cambie")
    ap.add_argument("--no-desest", action="store_true",
                    help="saltear la desestacionalización X-13")
    ap.add_argument("--x13-out", metavar="DIR",
                    help="guardar la salida de X-13 (html/factores/diagnósticos) en DIR")
    args = ap.parse_args(argv)
    urllib3.disable_warnings()  # cert de acero.org.ar (verify=False)

    rep = report.Report("acero", "run")
    conn = db.get_conn()
    try:
        try:
            res = source.get_latest()
        except Exception as e:
            rep.error(f"bajando/parseando: {e}")
            rep.summary()
            return
        if not res or not res[0]:
            rep.error("no se encontró PDF de Cifras o no se parseó ninguna fila")
            rep.summary()
            return
        data, url = res
        rep.info(f"fuente: {url} | meses: {min(data):%Y-%m}..{max(data):%Y-%m}")
        # La CAA a veces sube el PDF de Cifras sin linkearlo en la página (ver source.py), así
        # que el descubrimiento por link puede quedar atrás de lo ya cargado. Se avisa: sin
        # esto, la corrida son trece 'sin_cambios' y la situación pasa inadvertida.
        prev_max = db.last_date(
            conn, table=config.TABLE,
            where="serie = %s and estado is distinct from 'desestacionalizado'",
            where_params=(config.MAIN_SERIE,))
        if prev_max and max(data) < prev_max:
            rep.info(f"OJO: el PDF linkeado llega a {max(data):%Y-%m} pero ya hay datos hasta "
                     f"{prev_max:%Y-%m}, cargados de un PDF aún sin link. Revisar si hay uno "
                     f"más nuevo sin linkear antes de dar la fuente por atrasada.")
        for fecha in sorted(data):
            valor = data[fecha]
            status = db.insert_if_changed(
                conn, table=config.TABLE, key_cols=config.KEY_COLS,
                key_vals=[config.MAIN_SERIE, fecha], value_cols=config.VALUE_COLS,
                row={"valor": None if valor is None else float(valor)},
                estado="definitivo", fuente=url, force=args.force,
            )
            rep.item(f"{fecha:%Y-%m} {config.MAIN_SERIE}", status, valor=valor)
        rep.summary()

        if args.no_desest:
            pass
        elif not rep.changed:
            print("sin datos nuevos: no se desestacionaliza")
        else:
            seasonal.run_desest(conn, "acero",
                                desest_params.build_jobs("acero", keep_dir=args.x13_out))
    finally:
        conn.close()


if __name__ == "__main__":
    main()
