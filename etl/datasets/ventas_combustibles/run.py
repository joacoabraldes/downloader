"""ETL incremental de ventas de derivados al mercado interno (Sec. Energía -> Supabase).

Un solo POST trae los 51 productos por mes desde 2010-01, así que se baja una vez y la ventana
de meses se aplica en memoria (ver `source.py`).

Los totales (`gasoil_mas_nafta`, `gasoil`, `nafta`, ...) NO se guardan como filas: se derivan en
la vista `etl_ventas_combustibles_totales`, que es lo que permite redefinir un total sin backfill.

Al final, sólo si hubo datos nuevos, desestacionaliza `gasoil`, `nafta` y `glp` (X-13) leyendo de
esa misma vista de totales. `gasoil_mas_nafta` NO se ajusta directo: se deriva sumando las dos
componentes ajustadas, porque tienen estacionalidad opuesta y el ajuste directo del agregado
rompería la identidad (ver `etl/series_desest.toml` y `schema.sql`).

Flags:
  --month YYYY-MM        procesar solo ese mes
  --months-back N        últimos N meses a revisar (override de la ventana auto)
  --all                  procesar todos los meses que trae la fuente (backfill inicial)
  --force                insertar snapshot aunque no haya cambiado
  --no-desest            saltear la desestacionalización X-13
  --x13-out DIR          guardar la salida completa de X-13 en DIR

Para recalcular la desest SIN bajar nada: `python -m etl redesest ventas_combustibles`.
"""
from __future__ import annotations

import argparse
import datetime as dt

import urllib3

from etl.core import db, desest_params, report, seasonal, window
from . import config, source


def _guardar(conn, rep, fecha, productos, fuente, *, force: bool) -> None:
    """Snapshotea los productos del mes.

    Se imprime UNA línea por mes con el total en m3 (la magnitud que se sigue) y los 51
    productos se cuentan con `tally`: con `--all` son 199 meses x 51 series y una línea por
    serie dejaría el log inservible. Los contadores del `resumen [...]` los incluyen igual.
    """
    for serie, valor in sorted(productos.items()):
        rep.tally(db.insert_if_changed(
            conn, table=config.TABLE, key_cols=config.KEY_COLS,
            key_vals=[serie, fecha], value_cols=config.VALUE_COLS,
            row={"valor": float(valor)},
            estado="definitivo", fuente=fuente, force=force,
        ))
    m3 = sum(v for s, v in productos.items()
             if config.UNIDAD_POR_SERIE.get(s) == "(m3)")
    rep.info(f"{fecha:%Y-%m}  productos={len(productos)}  total_m3={m3:,.0f}".replace(",", "."))


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(
        prog="etl ventas_combustibles",
        description="ETL de ventas de derivados al mercado interno (Sec. Energía)")
    ap.add_argument("--month", help="mes puntual YYYY-MM (ignora --months-back)")
    ap.add_argument("--months-back", type=int,
                    help="últimos N meses a revisar (override de la ventana auto)")
    ap.add_argument("--all", action="store_true",
                    help="procesar todos los meses que trae la fuente (backfill inicial)")
    ap.add_argument("--force", action="store_true", help="insertar aunque no cambie")
    ap.add_argument("--no-desest", action="store_true",
                    help="saltear la desestacionalización X-13")
    ap.add_argument("--x13-out", metavar="DIR",
                    help="guardar la salida de X-13 (html/factores/diagnósticos) en DIR")
    args = ap.parse_args(argv)
    urllib3.disable_warnings()  # cert incompleto de estadisticas.energia.gob.ar (verify=False)

    conn = db.get_conn()
    try:
        rep = report.Report("ventas_combustibles", "run")
        try:
            datos, desconocidos = source.get_ventas()
        except Exception as e:
            rep.error(f"no se pudo leer la fuente: {e}")
            rep.summary()
            return

        # Un producto que no está en el catálogo SE GUARDA igual (perder el dato es peor) pero
        # se reporta como falla: si es un refinado nuevo, hasta que se lo clasifique queda fuera
        # de los totales y los subestima en silencio.
        for p in sorted(desconocidos):
            rep.error(f"producto fuera del catálogo: {p!r} — sumarlo a config.CATALOGO y al "
                      f"seed de la dimensión, o va a quedar fuera de los totales")

        if args.all:
            hoy = dt.date.today()
            months = window.month_range(min(datos), dt.date(hoy.year, hoy.month, 1))
        else:
            months = window.target_months(conn, table=config.TABLE, month=args.month,
                                          months_back=args.months_back)
        rep.info(f"fuente: Superset Sec. Energia | la fuente trae {len(datos)} meses "
                 f"({min(datos):%Y-%m}..{max(datos):%Y-%m}) | ventana: "
                 f"{months[0]:%Y-%m}..{months[-1]:%Y-%m}")

        fuente = source.API_DATA
        for fecha in months:
            productos = datos.get(fecha)
            if not productos:
                # Antes de 2010 no es "no publicado", es que la fuente no llega hasta ahí.
                if fecha >= source.INICIO:
                    rep.note(f"{fecha:%Y-%m}", "no publicado")
                continue
            _guardar(conn, rep, fecha, productos, fuente, force=args.force)
        rep.summary()

        if args.no_desest:
            pass
        elif not rep.changed:
            print("sin datos nuevos: no se desestacionaliza")
        else:
            seasonal.run_desest(conn, "ventas_combustibles",
                                desest_params.build_jobs("ventas_combustibles",
                                                         keep_dir=args.x13_out))
    finally:
        conn.close()


if __name__ == "__main__":
    main()
