"""ETL incremental de acero crudo (Cámara Argentina del Acero).

Baja el último PDF de "Cifras" (descubriéndolo, porque el nombre es inconsistente: ver
source.py), parsea las ~13 filas mensuales y snapshotea acero crudo con
estado='definitivo': la cifra
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
        data, url, notas = res
        # Las notas van ANTES del chequeo: si algo salió raro en el descubrimiento, el
        # diagnóstico tiene que salir igual aunque después no se parsee ninguna fila.
        for nota in notas:  # PDF subido sin linkear, o una de las dos vías caída (ver source.py)
            rep.info(nota)
        if not data:
            rep.error("no se parseó ninguna fila del PDF de Cifras")
            rep.summary()
            return
        rep.info(f"fuente: {url} | meses: {min(data):%Y-%m}..{max(data):%Y-%m}")
        # Red de seguridad: el descubrimiento ya mira la biblioteca de medios además del link,
        # así que la fuente sólo puede quedar atrás de lo cargado si el PDF más nuevo
        # desapareció de las dos. Se avisa igual: sin esto, la corrida son trece
        # 'sin_cambios' y la situación pasa inadvertida.
        prev_max = db.last_date(
            conn, table=config.TABLE,
            where="serie = %s and estado is distinct from 'desestacionalizado'",
            where_params=(config.MAIN_SERIE,))
        if prev_max and max(data) < prev_max:
            rep.info(f"OJO: el PDF más nuevo que se encontró llega a {max(data):%Y-%m} pero ya "
                     f"hay datos hasta {prev_max:%Y-%m}. Revisar a mano la página y "
                     f"wp-json/wp/v2/media antes de dar la fuente por atrasada.")
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
