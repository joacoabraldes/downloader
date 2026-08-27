"""ETL incremental de insumos procesados por las refinerías (Sec. Energía -> Supabase).

Un solo POST trae los 36 conceptos por mes desde 2010-01, así que se baja una vez y la ventana
de meses se aplica en memoria (ver `source.py`).

Los agregados (`crudo_procesado`, `otros_insumos`, `total_procesado` y el crudo por cuenca) NO se
guardan como filas: se derivan en `etl_refinacion_totales` y aparecen en `etl_refinacion_actual`
marcados `tipo='agregado'`. Redefinir un agregado es editar una vista, no reingestar.

NO desestacionaliza: el dataset todavía no está en `etl/series_desest.toml`.

Flags:
  --month YYYY-MM        procesar solo ese mes
  --months-back N        últimos N meses a revisar (override de la ventana auto)
  --all                  procesar todos los meses que trae la fuente (backfill inicial)
  --force                insertar snapshot aunque no haya cambiado
"""
from __future__ import annotations

import argparse
import datetime as dt

import urllib3

from etl.core import db, report, window
from . import config, source


def _guardar(conn, rep, fecha, conceptos, fuente, *, force: bool) -> None:
    """Snapshotea los conceptos del mes.

    Una línea por MES con el crudo procesado (la magnitud que se sigue); los 36 conceptos se
    cuentan con `tally`. Con `--all` son 199 x 36 y una línea por concepto haría ilegible el log.
    """
    for serie, valor in sorted(conceptos.items()):
        rep.tally(db.insert_if_changed(
            conn, table=config.TABLE, key_cols=config.KEY_COLS,
            key_vals=[serie, fecha], value_cols=config.VALUE_COLS,
            row={"valor": float(valor)},
            estado="definitivo", fuente=fuente, force=force,
        ))
    crudo = sum(v for s, v in conceptos.items() if config.TIPO_POR_SERIE.get(s) == "crudo")
    total = sum(conceptos.values())
    rep.info(f"{fecha:%Y-%m}  conceptos={len(conceptos)}  crudo={crudo:,.0f} "
             f"({crudo / total * 100:.1f}% del total)".replace(",", "."))


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(
        prog="etl refinacion",
        description="ETL de insumos procesados por las refinerias (Sec. Energía)")
    ap.add_argument("--month", help="mes puntual YYYY-MM (ignora --months-back)")
    ap.add_argument("--months-back", type=int,
                    help="últimos N meses a revisar (override de la ventana auto)")
    ap.add_argument("--all", action="store_true",
                    help="procesar todos los meses que trae la fuente (backfill inicial)")
    ap.add_argument("--force", action="store_true", help="insertar aunque no cambie")
    args = ap.parse_args(argv)
    urllib3.disable_warnings()  # cert incompleto de estadisticas.energia.gob.ar (verify=False)

    conn = db.get_conn()
    try:
        rep = report.Report("refinacion", "run")
        try:
            datos, desconocidos = source.get_insumos()
        except Exception as e:
            rep.error(f"no se pudo leer la fuente: {e}")
            rep.summary()
            return

        # Un concepto fuera del catálogo SE GUARDA igual (perder el dato es peor) pero se reporta
        # como falla: si es crudo, hasta clasificarlo queda fuera de `crudo_procesado` y lo
        # subestima en silencio.
        for c in sorted(desconocidos):
            rep.error(f"concepto fuera del catálogo: {c!r} — sumarlo a config.CATALOGO y al seed "
                      f"de la dimensión, o va a quedar fuera de los agregados")

        if args.all:
            hoy = dt.date.today()
            months = window.month_range(min(datos), dt.date(hoy.year, hoy.month, 1))
        else:
            months = window.target_months(conn, table=config.TABLE, month=args.month,
                                          months_back=args.months_back)
        rep.info(f"fuente: Superset Sec. Energia (insumos a refineria) | la fuente trae "
                 f"{len(datos)} meses ({min(datos):%Y-%m}..{max(datos):%Y-%m}) | ventana: "
                 f"{months[0]:%Y-%m}..{months[-1]:%Y-%m}")

        for fecha in months:
            conceptos = datos.get(fecha)
            if not conceptos:
                if fecha >= source.INICIO:
                    rep.note(f"{fecha:%Y-%m}", "no publicado")
                continue
            _guardar(conn, rep, fecha, conceptos, source.API_DATA, force=args.force)
        rep.summary()
    finally:
        conn.close()


if __name__ == "__main__":
    main()
