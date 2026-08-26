"""Carga histórica (one-off) de escrituras de compraventa en CABA.

Hace dos cosas que el incremental no:

1. **Carga todos los informes** publicados (2016-02 → hoy), con `estado='definitivo'`: la
   cantidad de actos y las 4 series secundarias que el informe traiga (monto, hipotecas,
   monto medio en pesos y en dólares). Las secundarias tienen huecos y eso NO es una falla.
2. **Rellena los meses que la fuente nunca publicó** leyendo las planillas rodantes de un
   informe posterior, con `estado='relleno'`. El relleno es sólo de la CANTIDAD de actos: las
   planillas rodantes no traen las series secundarias.

Los 6 huecos conocidos al 2026-08 son 2016-04, 2016-09, 2017-01, 2022-12, 2023-01 y 2023-02.
No son un error de parseo: esos posts no existen en la categoría. Los seis se recuperan de las
planillas de informes posteriores (verificado uno por uno).

El relleno entra con un estado propio para que se distinga del número del informe del mes: si
alguna vez la fuente publica el informe faltante, su valor pasa a mandar en la vista `_actual`
sin que haya que borrar nada (ver `schema.sql`).

Idempotente: usa insert_if_changed, así que re-correrlo no duplica.

Uso:
  python -m etl escrituras_caba load-history            # informes + relleno de huecos
  python -m etl escrituras_caba load-history --no-relleno
"""
from __future__ import annotations

import argparse
import datetime as dt
import sys
import time

from etl.core import db, report
from . import config, source

# Cuántos informes posteriores mirar para tapar un hueco. Las planillas rodantes cubren ~12
# meses hacia atrás, así que con 12 alcanza; el barrido corta apenas encuentra el mes.
MAX_INFORMES_ADELANTE = 12
PAUSA = 0.4  # segundos entre descargas de planillas (son un .xls cada una)


def _mes_siguiente(d: dt.date) -> dt.date:
    return dt.date(d.year + (d.month == 12), d.month % 12 + 1, 1)


def huecos(fechas) -> list[dt.date]:
    """Meses sin informe entre el primero y el último publicados."""
    fechas = sorted(fechas)
    faltan, d = [], fechas[0]
    presentes = set(fechas)
    while d <= fechas[-1]:
        if d not in presentes:
            faltan.append(d)
        d = _mes_siguiente(d)
    return faltan


def rellenar(conn, rep, hueco, posts_por_fecha, *, force=False) -> bool:
    """Busca `hueco` en las planillas rodantes de los informes posteriores. True si lo cargó."""
    posteriores = [f for f in sorted(posts_por_fecha) if f > hueco][:MAX_INFORMES_ADELANTE]
    for fecha in posteriores:
        for url in source.rodantes_de(posts_por_fecha[fecha]["html"]):
            try:
                serie = source.leer_rodante(url)
            except Exception as e:
                rep.info(f"AVISO relleno {hueco:%Y-%m}: no se pudo leer {url} ({e})")
                continue
            time.sleep(PAUSA)
            if hueco in serie:
                status = db.insert_if_changed(
                    conn, table=config.TABLE, key_cols=config.KEY_COLS,
                    key_vals=[config.MAIN_SERIE, hueco], value_cols=config.VALUE_COLS,
                    row={"valor": float(serie[hueco])},
                    estado="relleno", fuente=url, force=force,
                )
                rep.item(f"{hueco:%Y-%m} (relleno)", status, actos=serie[hueco])
                return True
    rep.info(f"AVISO {hueco:%Y-%m}: sin informe propio y ninguna planilla posterior lo trae")
    return False


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="etl escrituras_caba load-history",
                                 description="Carga histórica de escrituras CABA (one-off).")
    ap.add_argument("--no-relleno", action="store_true",
                    help="no completar con planillas los meses sin informe")
    ap.add_argument("--force", action="store_true", help="re-insertar aunque no cambie")
    args = ap.parse_args(argv)

    rep = report.Report("escrituras_caba", "load-history")
    posts = source.get_posts()
    for p in [x for x in posts if x["error"]]:
        rep.error(f"informe sin parsear ({p['url']}): {p['error']}")
    datos = {p["date"]: p for p in posts if p["date"] and p["actos"] is not None}
    if not datos:
        print("No se leyó ningún informe.", file=sys.stderr)
        sys.exit(1)

    faltan = huecos(datos)
    rep.info(f"informes: {len(datos)} ({min(datos):%Y-%m}..{max(datos):%Y-%m}) | "
             f"meses sin informe: {len(faltan)}")

    conn = db.get_conn()
    try:
        for fecha in sorted(datos):
            p = datos[fecha]
            valores = {config.MAIN_SERIE: p["actos"], **{k: v for k, v in p["extras"].items()
                                                         if k in config.EXTRAS}}
            for serie, valor in valores.items():
                rep.tally(db.insert_if_changed(
                    conn, table=config.TABLE, key_cols=config.KEY_COLS,
                    key_vals=[serie, fecha], value_cols=config.VALUE_COLS,
                    row={"valor": float(valor)},
                    estado="definitivo", fuente=p["url"], force=args.force,
                ))
            if "monto_medio_descartado" in p["extras"]:
                rep.info(f"AVISO {fecha:%Y-%m}: monto_medio del texto "
                         f"({p['extras']['monto_medio_descartado']:.0f}) no cierra contra "
                         f"monto/cantidad; no se guarda")
        for serie in config.EXTRAS:
            n = sum(1 for p in datos.values() if serie in p["extras"])
            rep.info(f"{serie}: {n}/{len(datos)} informes lo traen")
        if not args.no_relleno:
            for hueco in faltan:
                rellenar(conn, rep, hueco, datos, force=args.force)
    finally:
        conn.close()
    rep.summary()


if __name__ == "__main__":
    main()
