"""ETL incremental de producción de hidrocarburos (Secretaría de Energía -> Supabase).

Baja de Superset la serie mensual de petróleo y la de gas, y snapshotea con estado='provisorio'
el total del mes y su desagregado por tipo de recurso. Al final, sólo si hubo datos nuevos o
actualizados, desestacionaliza los dos totales por separado (X-13).

A diferencia de los ETLs que piden un archivo por mes (automotriz, cemento), acá **un solo
request trae la serie entera** (2009 → último mes publicado, ~9 KB). Así que se baja una vez
por serie y la ventana de meses se aplica en memoria: no hay nada que ahorrar pidiendo menos.

Ventana de meses: por default se pone al día desde el último mes que hay en la base
(re-chequeando los últimos meses por revisiones) hasta hoy. Las revisiones importan más acá
que en otras fuentes: los productores rectifican declaraciones juradas hacia atrás y el
dashboard refleja el último estado, así que un mes ya cargado puede cambiar.

Flags:
  --month YYYY-MM        procesar solo ese mes
  --months-back N        últimos N meses a revisar (override de la ventana auto)
  --all                  procesar TODOS los meses que trae la fuente (2009→). Es el backfill
                         inicial: sin esto, una base recién cargada con `load-history` se queda
                         con el histórico del Excel en 2009-2026 y nunca ve el dato primario.
  --force                insertar snapshot aunque no haya cambiado
  --no-desest            saltear la desestacionalización X-13
  --x13-out DIR          guardar la salida completa de X-13 en DIR

Para recalcular la desestacionalización SIN bajar nada: `python -m etl redesest hidrocarburos`.
"""
from __future__ import annotations

import argparse
import datetime as dt

import urllib3

from etl.core import db, desest_params, report, seasonal, window
from . import config, source


def _guardar(conn, rep, serie, fecha, datos, fuente, *, force: bool) -> None:
    """Snapshotea el total del mes y sus tres tipos de recurso.

    El TOTAL se imprime como ítem y los tipos sólo se cuentan (`tally`): son 4 filas por serie
    y por mes, y con `--all` (211 meses x 2 series) el log se volvería ilegible. Los tipos
    igual entran en los contadores del `resumen [...]`, así que un cambio no se pierde.
    """
    status = db.insert_if_changed(
        conn, table=config.TABLE, key_cols=config.KEY_COLS,
        key_vals=[serie, fecha], value_cols=config.VALUE_COLS,
        row={"valor": datos.get("total")},
        estado="provisorio", fuente=fuente, force=force,
    )
    rep.item(f"{fecha:%Y-%m} {serie:9}", status, valor=round(datos["total"], 3))

    for tipo in config.TIPOS:
        valor = datos.get(tipo)
        rep.tally(db.insert_if_changed(
            conn, table=config.TABLE, key_cols=config.KEY_COLS,
            key_vals=[f"{serie}_{tipo}", fecha], value_cols=config.VALUE_COLS,
            row={"valor": None if valor is None else float(valor)},
            estado="provisorio", fuente=fuente, force=force,
        ))


def process_serie(conn, rep, serie, months, *, force: bool) -> None:
    """Baja la serie completa y snapshotea los meses de la ventana que la fuente ya publicó."""
    fuente = source.chart_url(serie)
    try:
        data = source.get_serie(serie)
    except Exception as e:  # red caída, CSV con un concepto nuevo, etc.
        rep.error(f"{serie}: {e}")
        return
    rep.info(f"{serie}: la fuente trae {len(data)} meses "
             f"({min(data):%Y-%m}..{max(data):%Y-%m})")
    for fecha in months:
        datos = data.get(fecha)
        if not datos:
            # Antes de 2009 no es "no publicado", es que la fuente no llega: eso lo cubre el
            # histórico y no tiene por qué ensuciar el log de cada corrida.
            if fecha >= source.INICIO:
                rep.note(f"{fecha:%Y-%m} {serie:9}", "no publicado")
            continue
        _guardar(conn, rep, serie, fecha, datos, fuente, force=force)


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="etl hidrocarburos",
                                 description="ETL de producción de petróleo y gas (Sec. Energía)")
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
        if args.all:
            # Desde el primer mes de la fuente hasta hoy. `month_range` es barato y los meses
            # que la fuente todavía no publicó se saltean solos en `process_serie`.
            hoy = dt.date.today()
            months = window.month_range(source.INICIO, dt.date(hoy.year, hoy.month, 1))
        else:
            months = window.target_months(conn, table=config.TABLE, month=args.month,
                                          months_back=args.months_back)
        rep = report.Report("hidrocarburos", "run")
        rep.info(f"fuente: Superset Sec. Energia | meses: "
                 f"{months[0]:%Y-%m}..{months[-1]:%Y-%m}")
        for serie in config.TOTALES:
            process_serie(conn, rep, serie, months, force=args.force)
        rep.summary()

        if args.no_desest:
            pass
        elif not rep.changed:
            print("sin datos nuevos: no se desestacionaliza")
        else:
            seasonal.run_desest(conn, "hidrocarburos",
                                desest_params.build_jobs("hidrocarburos",
                                                         keep_dir=args.x13_out))
    finally:
        conn.close()


if __name__ == "__main__":
    main()
