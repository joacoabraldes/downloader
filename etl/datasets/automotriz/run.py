"""ETL incremental de la industria automotriz (ADEFA -> Supabase).

Por cada mes objetivo baja el PDF de ADEFA y snapshotea las 3 series (produccion,
ventas, expo) con estado='provisorio'. Al final, sólo si hubo datos nuevos o
actualizados, desestacionaliza cada serie por separado (X-13).

Ventana de meses: por default se pone al día desde el último mes que hay en la base
(re-chequeando los últimos meses por revisiones) hasta hoy, así nunca se "saltea" el
último mes publicado. `--month` y `--months-back` siguen como override.

Respaldo: los meses que estadísticas todavía no publicó se completan desde la gacetilla de
prensa, que ADEFA sube antes (ver `source.py`). Adelanta el dato ~1 mes. Entra con el mismo
`estado='provisorio'` y se tapa solo cuando estadísticas publica.

Flags:
  --month YYYY-MM        procesar solo ese mes
  --months-back N        últimos N meses a revisar (override de la ventana auto)
  --force                insertar snapshot aunque no haya cambiado
  --no-desest            saltear la desestacionalización X-13
  --x13-out DIR          guardar la salida completa de X-13 en DIR
  --no-prensa-fallback   no completar con la gacetilla los meses sin publicar

Para recalcular la desestacionalización SIN bajar el PDF: `python -m etl redesest automotriz`.
"""
from __future__ import annotations

import argparse
import re

import urllib3

from etl.core import db, desest_params, report, seasonal, window
from . import config, source


def _guardar(conn, rep, fecha, data, fuente, *, force: bool) -> None:
    """Snapshotea las 3 series del mes, reportando cada una."""
    for serie in config.SERIES:
        valor = data.get(serie)
        status = db.insert_if_changed(
            conn, table=config.TABLE, key_cols=config.KEY_COLS,
            key_vals=[serie, fecha], value_cols=config.VALUE_COLS,
            row={"valor": None if valor is None else float(valor)},
            estado="provisorio", fuente=fuente, force=force,
        )
        rep.item(f"{fecha:%Y-%m} {serie:11}", status, valor=valor)


def process_month(conn, rep, fecha, *, force: bool) -> bool:
    """Baja el PDF de estadísticas y snapshotea el mes. False si no está publicado."""
    try:
        data = source.get_month(fecha.year, fecha.month)
        fuente = source.pdf_url(fecha.year, fecha.month)
    except Exception as e:  # red caída, PDF inesperado, etc.
        rep.note(fecha, f"ERROR {e}", status="saltado", failure=True)
        return True  # hubo falla, no es un hueco que deba cubrir el respaldo
    if not data:
        rep.note(fecha, "no publicado")
        return False
    _guardar(conn, rep, fecha, data, fuente, force=force)
    return True


def _ultimo_id_prensa(conn) -> int | None:
    """Mayor id de gacetilla ya usado, para arrancar el barrido de ahí y no del principio."""
    with conn.cursor() as cur:
        cur.execute(f"select fuente from {config.TABLE} "
                    f"where fuente like %s order by ingested_at desc limit 1",
                    (f"{source.PRENSA_BASE}%",))
        row = cur.fetchone()
    if not row or not row[0]:
        return None
    m = re.search(r"id=(\d+)", row[0])
    return int(m.group(1)) if m else None


def _completar_con_prensa(conn, rep, *, faltantes, ultimo, force: bool) -> None:
    """Completa desde la gacetilla de prensa los meses que estadísticas todavía no publicó.

    ADEFA sube el informe a prensa antes que a estadísticas, así que esto adelanta el dato ~1
    mes. Es el MISMO PDF con los mismos números (verificado 9/9 en abril, mayo y junio 2026),
    y entra igual que el primario con `estado='provisorio'`: cuando estadísticas publique, el
    valor va a ser idéntico y `insert_if_changed` lo va a reportar `sin_cambios`. La procedencia
    queda en `fuente`, que guarda la URL de la gacetilla.

    Sólo completa **hacia adelante** (meses > `ultimo`), nunca rellena huecos viejos: si un mes
    ya está cubierto por estadísticas no hay nada que adelantar.

    Si el respaldo falla NO se marca la corrida como fallida: la fuente primaria ya cumplió su
    trabajo y mandar mail por un canal de respaldo sería ruido. Queda como AVISO y la corrida
    siguiente reintenta. Mismo criterio que el fallback de `aves`.
    """
    pendientes = [f for f in faltantes if ultimo is None or f > ultimo]
    if not pendientes:
        return
    try:
        desde = _ultimo_id_prensa(conn)
    except Exception as e:
        rep.info(f"AVISO fallback prensa: no se pudo leer el último id usado ({e})")
        desde = None
    for fecha in pendientes:
        try:
            res = source.get_month_prensa(fecha.year, fecha.month, desde_id=desde)
        except Exception as e:
            rep.info(f"AVISO fallback prensa {fecha:%Y-%m}: {e}")
            continue
        if not res:
            rep.info(f"fallback prensa {fecha:%Y-%m}: la gacetilla tampoco está publicada")
            continue
        data, url = res
        rep.info(f"fallback prensa {fecha:%Y-%m}: {url}")
        _guardar(conn, rep, fecha, data, url, force=force)


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="etl automotriz",
                                 description="ETL automotriz ADEFA")
    ap.add_argument("--month", help="mes puntual YYYY-MM (ignora --months-back)")
    ap.add_argument("--months-back", type=int,
                    help="últimos N meses a revisar (override de la ventana auto)")
    ap.add_argument("--force", action="store_true", help="insertar aunque no cambie")
    ap.add_argument("--no-desest", action="store_true",
                    help="saltear la desestacionalización X-13")
    ap.add_argument("--x13-out", metavar="DIR",
                    help="guardar la salida de X-13 (html/factores/diagnósticos) en DIR")
    ap.add_argument("--no-prensa-fallback", action="store_true",
                    help="no completar con la gacetilla de prensa los meses que estadísticas "
                         "todavía no publicó")
    args = ap.parse_args(argv)
    urllib3.disable_warnings()  # cert de ADEFA (verify=False)

    conn = db.get_conn()
    try:
        months = window.target_months(conn, table=config.TABLE, month=args.month,
                                      months_back=args.months_back)
        rep = report.Report("automotriz", "run")
        rep.info(f"fuente: ADEFA PDF | meses: {months[0]:%Y-%m}..{months[-1]:%Y-%m}")
        # `ultimo` se lee ANTES del loop: es hasta dónde llegaba la serie al empezar, y es lo
        # que decide qué meses puede adelantar el respaldo. Leerlo después incluiría lo que
        # acaba de insertar el primario y el fallback no tendría nada que hacer.
        ultimo = db.last_date(conn, table=config.TABLE,
                              where="estado is distinct from %s",
                              where_params=("desestacionalizado",))
        faltantes = [f for f in months if not process_month(conn, rep, f, force=args.force)]
        if faltantes and not args.no_prensa_fallback:
            _completar_con_prensa(conn, rep, faltantes=faltantes, ultimo=ultimo,
                                  force=args.force)
        rep.summary()

        if args.no_desest:
            pass
        elif not rep.changed:
            print("sin datos nuevos: no se desestacionaliza")
        else:
            seasonal.run_desest(conn, "automotriz",
                                desest_params.build_jobs("automotriz", keep_dir=args.x13_out))
    finally:
        conn.close()


if __name__ == "__main__":
    main()
