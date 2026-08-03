"""ETL incremental de compras y DJVE de granos (semanal).

Se pone al día desde la última semana que hay en la base, re-chequeando las últimas `--lookback`
semanas: MAGyP revisa cifras ya publicadas (las marca con (*) en la página), así que volver a
leerlas es lo que hace que la revisión entre. El append-only del núcleo se encarga de que
re-leer una semana sin cambios no inserte nada.

La lista de semanas sale del índice de cada año, no de generar los miércoles: la fuente saltea
semanas (feriados, semanas acumuladas en la siguiente) y algún link del índice apunta a una
página que no existe.

Con la tabla vacía NO hace el histórico completo: procesa las últimas semanas y avisa. Para los
21 años está `python -m etl compras_granos load-history`.

Flags:
  --semana AAAA-MM-DD   procesar sólo esa semana
  --weeks-back N        procesar las últimas N semanas publicadas (override de la ventana auto)
  --lookback N          semanas hacia atrás a re-chequear por revisiones (default: 3)
  --force               insertar snapshot aunque no haya cambiado
"""
from __future__ import annotations

import argparse
import datetime as dt
import time

import requests
import urllib3

from etl.core import db, report
from . import config, source

LOOKBACK_DEFAULT = 3   # semanas re-leídas para captar revisiones de MAGyP
ARRANQUE_VACIO = 8     # semanas a traer si la tabla está vacía (el histórico va por load-history)
PAUSA = 0.3


def _semanas_publicadas(anios: list[int], rep: report.Report) -> list[dt.date]:
    """Fechas de corte publicadas en los índices de esos años, ascendentes."""
    fechas: set[dt.date] = set()
    for anio in anios:
        try:
            html = source.fetch_html(source.url_anio(anio))
        except Exception as e:  # noqa: BLE001
            rep.error(f"indice {anio}: {e}")
            continue
        fechas.update(f for f in source.parse_indice_anual(html) if f.year == anio)
    return sorted(fechas)


def _objetivo(conn, publicadas: list[dt.date], semana: str | None,
              weeks_back: int | None, lookback: int) -> list[dt.date]:
    """Semanas a procesar, siempre dentro de las que la fuente publica."""
    if semana:
        return [dt.datetime.strptime(semana, "%Y-%m-%d").date()]
    if weeks_back is not None:
        return publicadas[-weeks_back:] if weeks_back > 0 else publicadas
    ultima = db.last_date(conn, table=config.TABLE)
    if ultima is None:
        return publicadas[-ARRANQUE_VACIO:]
    desde = ultima - dt.timedelta(weeks=lookback)
    return [f for f in publicadas if f >= desde]


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="etl compras_granos",
                                 description="ETL incremental de compras y DJVE de granos (semanal).")
    ap.add_argument("--semana", metavar="AAAA-MM-DD", help="procesar sólo esta semana")
    ap.add_argument("--weeks-back", type=int, metavar="N",
                    help="últimas N semanas publicadas (override de la ventana auto)")
    ap.add_argument("--lookback", type=int, default=LOOKBACK_DEFAULT, metavar="N",
                    help=f"semanas a re-chequear por revisiones (default: {LOOKBACK_DEFAULT})")
    ap.add_argument("--force", action="store_true", help="insertar aunque no cambie")
    args = ap.parse_args(argv)
    urllib3.disable_warnings()  # certs de gov.ar (verify=False)

    hoy = dt.date.today()
    rep = report.Report("compras_granos", "run")
    conn = db.get_conn()
    try:
        ultima = db.last_date(conn, table=config.TABLE)
        # Con la base al día alcanza el año en curso; si está atrasada, se suma el año de su
        # última semana para no dejar un hueco al cruzar el 1 de enero.
        anios = sorted({hoy.year, (ultima.year if ultima else hoy.year)})
        publicadas = _semanas_publicadas(anios, rep)
        if not publicadas:
            rep.error("no se pudo leer ninguna semana de los índices")
            rep.summary()
            return
        # Sólo cuando la ventana es la automática: con --semana / --weeks-back el usuario ya
        # eligió qué traer y el aviso sería falso.
        if ultima is None and not args.semana and args.weeks_back is None:
            rep.info(f"tabla vacía: se traen las últimas {ARRANQUE_VACIO} semanas. "
                     f"Para el histórico completo: python -m etl compras_granos load-history")

        objetivo = _objetivo(conn, publicadas, args.semana, args.weeks_back, args.lookback)
        rep.info(f"publicadas: {publicadas[0]}..{publicadas[-1]} | "
                 f"a procesar: {len(objetivo)} semana(s)")

        for fecha in objetivo:
            if fecha not in publicadas and not args.semana:
                rep.note(f"{fecha}", "no publicada")
                continue
            url = source.url_semana(fecha)
            try:
                filas = source.parse_semana(source.fetch_html(url), fecha)
            except requests.HTTPError as e:
                if e.response is not None and e.response.status_code == 404:
                    rep.note(f"{fecha}", "pagina inexistente (404)")
                else:
                    rep.note(f"{fecha}", f"error HTTP: {e}", failure=True)
                continue
            except source.SemanaSinDatos:
                rep.note(f"{fecha}", "la fuente avisa que esta semana se acumuló en otra")
                continue
            except Exception as e:  # noqa: BLE001 - fuente caída o parser roto: falla de corrida
                rep.note(f"{fecha}", f"bajando/parseando: {e}", failure=True)
                continue
            finally:
                time.sleep(PAUSA)

            estados: dict[str, int] = {}
            for f in filas:
                status = db.insert_if_changed(
                    conn, table=config.TABLE, key_cols=config.KEY_COLS,
                    key_vals=[f["cultivo"], f["cosecha"], f["sector"], f["metrica"], f["date"]],
                    value_cols=config.VALUE_COLS, row={"valor": f["valor"]},
                    estado=config.ESTADO, fuente=url, force=args.force,
                    extra={"corte": f["corte"]},
                )
                rep.tally(status)
                estados[status] = estados.get(status, 0) + 1
            rep.info(f"{fecha}  filas={len(filas)}  " +
                     "  ".join(f"{k}={v}" for k, v in sorted(estados.items())))
    finally:
        conn.close()
    rep.summary()


if __name__ == "__main__":
    main()
