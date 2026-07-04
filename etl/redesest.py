"""Recalcula la serie desestacionalizada (X-13) de cada dataset desde el histórico en la base.

No baja nada de la web: lee la serie observada de `<tabla>_actual` y reescribe las filas
`estado='desestacionalizado'` con la parametrización del cuadro (`etl/series_desest.toml`).
Corre los datasets de corrido, sin intervención.

Por default hace UPSERT: pisa cada `(serie, date)` en el lugar (conserva la continuidad por
fila). Con `--clean` borra primero las filas desestacionalizadas de cada tabla y las regenera
(rebuild limpio; sirve para pizarra limpia y para borrar series huérfanas que salieron del
cuadro). El insumo (`<tabla>_actual`, que excluye la desest) nunca se toca.

Uso:
  python -m etl redesest                 # todos los datasets del cuadro, UPSERT
  python -m etl redesest --clean         # borra las desest y regenera (rebuild limpio)
  python -m etl redesest granos cemento  # solo esos
  python -m etl redesest --x13-out DIR   # además guarda la salida completa de X-13
"""
from __future__ import annotations

import argparse
import sys

from etl.core import db, desest_params, seasonal


def main(argv=None) -> None:
    known = desest_params.datasets()
    ap = argparse.ArgumentParser(
        prog="etl redesest",
        description="Recalcula la desestacionalización desde la base (sin bajar de la web).")
    ap.add_argument("datasets", nargs="*", metavar="DATASET",
                    help=f"datasets a recalcular (default: todos: {', '.join(known)})")
    ap.add_argument("--clean", action="store_true",
                    help="borrar las filas desestacionalizadas antes de regenerar (rebuild limpio)")
    ap.add_argument("--x13-out", metavar="DIR",
                    help="guardar la salida de X-13 (html/factores/diagnósticos) en DIR")
    args = ap.parse_args(argv)

    targets = args.datasets or known
    desconocidos = [d for d in targets if d not in known]
    if desconocidos:
        print(f"dataset(s) desconocido(s): {', '.join(desconocidos)} "
              f"(conocidos: {', '.join(known)})", file=sys.stderr)
        sys.exit(2)

    # Nunca borrar sin poder regenerar: si no hay binario x13as, abortar sin tocar nada.
    if not seasonal.x13_available():
        print("x13as no encontrado (setear X13PATH). No se recalcula ni se borra nada.",
              file=sys.stderr)
        sys.exit(2)

    conn = db.get_conn()
    try:
        for ds in targets:
            if args.clean:
                table = desest_params.table_for(ds)
                with conn.cursor() as cur:
                    cur.execute(f"delete from {table} where estado = 'desestacionalizado'")
                    borradas = cur.rowcount
                conn.commit()
                print(f"[{ds} / redesest]  borradas {borradas} filas desestacionalizadas "
                      f"de {table}")
            seasonal.run_desest(conn, ds,
                                desest_params.build_jobs(ds, keep_dir=args.x13_out))
    finally:
        conn.close()


if __name__ == "__main__":
    main()
