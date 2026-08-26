"""ETL incremental de escrituras de compraventa en CABA (Colegio de Escribanos -> Supabase).

Baja la categoría entera por la API REST del WordPress (2 requests, ~120 informes) y snapshotea
con estado='definitivo' los meses de la ventana: la cantidad de actos y, cuando el informe las
trae, las 4 series secundarias (monto, hipotecas, monto medio en pesos y en dólares).

Como el listado viene completo en cada corrida, no hay endpoint "por mes" ni hace falta: la
ventana se aplica en memoria.

NO desestacionaliza: la serie no se ajusta (ver `schema.sql` y `etl/series_desest.toml`).

Ventana de meses: por default se pone al día desde el último mes que hay en la base
(re-chequeando los últimos meses por revisiones) hasta hoy. Las revisiones existen: hay meses
donde el texto del informe y una planilla posterior difieren en 1 acto.

Flags:
  --month YYYY-MM        procesar solo ese mes
  --months-back N        últimos N meses a revisar (override de la ventana auto)
  --all                  procesar todos los informes publicados (backfill; ver load_history)
  --force                insertar snapshot aunque no haya cambiado

Un informe que no parsea se reporta como FALLA, no se saltea en silencio.
"""
from __future__ import annotations

import argparse
import datetime as dt

from etl.core import db, report, window
from . import config, source


def _una(conn, serie, fecha, valor, fuente, *, estado, force) -> str:
    return db.insert_if_changed(
        conn, table=config.TABLE, key_cols=config.KEY_COLS,
        key_vals=[serie, fecha], value_cols=config.VALUE_COLS,
        row={"valor": float(valor)},
        estado=estado, fuente=fuente, force=force,
    )


def _guardar(conn, rep, fecha, informe, fuente, *, estado="definitivo", force=False) -> None:
    """Guarda la cantidad de actos y las series secundarias que el informe traiga.

    La cantidad se imprime como ítem; las secundarias se cuentan (`tally`) y sólo se listan en
    la línea del mes. Una secundaria ausente NO es falla: la redacción cambió mucho en 10 años
    y hay informes que son sólo la lista de descargas (ver `source.extras_del_texto`).
    """
    extras = informe.get("extras") or {}
    status = _una(conn, config.MAIN_SERIE, fecha, informe["actos"], fuente,
                  estado=estado, force=force)
    presentes = []
    for serie in config.EXTRAS:
        if serie not in extras:
            continue
        rep.tally(_una(conn, serie, fecha, extras[serie], fuente, estado=estado, force=force))
        presentes.append(serie)
    rep.item(f"{fecha:%Y-%m}", status, actos=informe["actos"],
             extras=",".join(presentes) or None)
    # El monto medio que la fuente publicó con un typo de escala (ver source): se avisa y no entra.
    if "monto_medio_descartado" in extras:
        rep.info(f"AVISO {fecha:%Y-%m}: monto_medio del texto "
                 f"({extras['monto_medio_descartado']:.0f}) no cierra contra monto/cantidad; "
                 f"no se guarda")


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(
        prog="etl escrituras_caba",
        description="ETL de escrituras de compraventa en CABA (Colegio de Escribanos)")
    ap.add_argument("--month", help="mes puntual YYYY-MM (ignora --months-back)")
    ap.add_argument("--months-back", type=int,
                    help="últimos N meses a revisar (override de la ventana auto)")
    ap.add_argument("--all", action="store_true",
                    help="procesar todos los informes publicados")
    ap.add_argument("--force", action="store_true", help="insertar aunque no cambie")
    args = ap.parse_args(argv)

    conn = db.get_conn()
    try:
        rep = report.Report("escrituras_caba", "run")
        try:
            posts = source.get_posts()
        except Exception as e:
            rep.error(f"no se pudo leer la categoría: {e}")
            rep.summary()
            return

        # Un informe que no parsea es una falla: la fuente cambió la redacción y hay que mirarlo.
        for p in [x for x in posts if x["error"]]:
            rep.error(f"informe sin parsear ({p['url']}): {p['error']}")
        datos = {p["date"]: p for p in posts if p["date"] and p["actos"] is not None}
        if not datos:
            rep.error("la categoría no devolvió ningún informe parseable")
            rep.summary()
            return

        if args.all:
            hoy = dt.date.today()
            months = window.month_range(min(datos), dt.date(hoy.year, hoy.month, 1))
        else:
            months = window.target_months(conn, table=config.TABLE, month=args.month,
                                          months_back=args.months_back)
        rep.info(f"fuente: Colegio de Escribanos CABA | informes: {len(datos)} "
                 f"({min(datos):%Y-%m}..{max(datos):%Y-%m}) | ventana: "
                 f"{months[0]:%Y-%m}..{months[-1]:%Y-%m}")

        for fecha in months:
            p = datos.get(fecha)
            if not p:
                # Antes del primer informe no es "no publicado", es que la fuente no llega.
                if fecha >= source.INICIO:
                    rep.note(f"{fecha:%Y-%m}", "no publicado")
                continue
            _guardar(conn, rep, fecha, p, p["url"], force=args.force)
        rep.summary()
    finally:
        conn.close()


if __name__ == "__main__":
    main()
