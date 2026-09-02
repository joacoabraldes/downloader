"""ETL incremental de transferencias de automotores (DNRPA -> Supabase).

Baja el cuadro anual de la DNRPA y snapshotea con estado='provisorio' el TOTAL PAÍS de cada
mes de la ventana. Al final, sólo si hubo datos nuevos o actualizados, desestacionaliza (X-13).

**Un request trae el año entero**, así que la ventana de meses se agrupa por año y se baja un
cuadro por año (no uno por mes). Es el mismo criterio que hidrocarburos: no hay nada que
ahorrar pidiendo menos.

## No hay `load-history`: el backfill es `--all`

La fuente sirve el histórico completo (1995 → hoy) con el MISMO formulario y el MISMO parser
que el incremental, así que no hace falta un segundo camino de carga desde una planilla. Igual
que refinacion o ventas_combustibles.

`etl/datasets/transferencias/data/transfer_autos.xlsx` está en el repo como REFERENCIA DE
CALIBRACIÓN de X-13 (columna desestacionalizada), no como fuente de datos, y **tiene cuatro
meses mal**: ver `scripts/calibrar_transferencias.py`.

## Todo entra como 'provisorio', y es a propósito

La DNRPA no marca ningún mes como cerrado: los registros seccionales siguen informando
trámites después del cierre y el cuadro se corrige hacia arriba. Medido contra la planilla de
referencia, agosto-2026 pasó de 155.246 a 155.717 (+0,3%) y los 375 meses anteriores no se
movieron ni un trámite. O sea: revisa el último mes, y sólo ése. Sin señal de "definitivo",
llamar definitivo a algo sería inventarlo.

Flags:
  --month YYYY-MM   procesar solo ese mes
  --months-back N   últimos N meses a revisar (override de la ventana auto)
  --all             procesar todos los meses que trae la fuente (1995 →). Es el backfill inicial.
  --force           insertar snapshot aunque no haya cambiado
  --no-desest       saltear la desestacionalización X-13
  --x13-out DIR     guardar la salida completa de X-13 en DIR

Para recalcular la desestacionalización SIN bajar nada: `python -m etl redesest transferencias`.
"""
from __future__ import annotations

import argparse
import datetime as dt
from collections import defaultdict

from etl.core import db, desest_params, report, seasonal, window
from . import config, source


def _por_anio(months: list[dt.date]) -> dict[int, list[dt.date]]:
    """Agrupa la ventana por año: un request de la fuente cubre los 12 meses de un año."""
    out: dict[int, list[dt.date]] = defaultdict(list)
    for m in months:
        out[m.year].append(m)
    return out


def process_serie(conn, rep, serie: str, months: list[dt.date], *, force: bool) -> None:
    """Baja un cuadro por año de la ventana y snapshotea los meses que la fuente ya publicó."""
    codigo_tipo = config.SERIES_TIPO[serie]
    for anio, meses in sorted(_por_anio(months).items()):
        url = source.consulta_url(anio, codigo_tipo)
        try:
            data = source.get_anio(anio, codigo_tipo)
        except Exception as e:  # red caída, cuadro que no cierra, layout cambiado
            rep.error(f"{serie} {anio}: {e}")
            continue
        for fecha in meses:
            valor = data.get(fecha)
            if valor is None:
                # Antes de 1995 no es "no publicado", es que la fuente no llega: no ensucia el log.
                if fecha >= config.INICIO:
                    rep.note(f"{fecha:%Y-%m} {serie:6}", "no publicado")
                continue
            status = db.insert_if_changed(
                conn, table=config.TABLE, key_cols=config.KEY_COLS,
                key_vals=[serie, fecha], value_cols=config.VALUE_COLS,
                row={"valor": valor}, estado="provisorio", fuente=url, force=force,
            )
            rep.item(f"{fecha:%Y-%m} {serie:6}", status, valor=valor)


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(
        prog="etl transferencias",
        description="ETL de transferencias de automotores (DNRPA)")
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

    conn = db.get_conn()
    try:
        if args.all:
            hoy = dt.date.today()
            months = window.month_range(config.INICIO, dt.date(hoy.year, hoy.month, 1))
        else:
            months = window.target_months(conn, table=config.TABLE, month=args.month,
                                          months_back=args.months_back)
        rep = report.Report("transferencias", "run")
        rep.info(f"fuente: DNRPA cuadro por provincia | meses: "
                 f"{months[0]:%Y-%m}..{months[-1]:%Y-%m}")
        for serie in config.SERIES:
            process_serie(conn, rep, serie, months, force=args.force)
        rep.summary()

        if args.no_desest:
            pass
        elif not rep.changed:
            print("sin datos nuevos: no se desestacionaliza")
        else:
            seasonal.run_desest(conn, "transferencias",
                                desest_params.build_jobs("transferencias",
                                                         keep_dir=args.x13_out))
    finally:
        conn.close()


if __name__ == "__main__":
    main()
