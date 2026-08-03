"""Carga histórica (one-off) de compras y DJVE de granos: 2005 -> hoy, semana por semana.

La fuente no publica ningún archivo consolidado: el histórico son ~1.100 páginas HTML, una por
semana, repartidas en tres formatos distintos (ver source.py). Este comando recorre el índice de
cada año, baja cada semana y la inserta.

La decisión de cómo insertar se toma AÑO POR AÑO, no una vez para toda la corrida:

  - año sin ninguna fila en la base -> `bulk_insert` (append-only sin dedup, una transacción por
    año). Son cientos de miles de filas y el camino normal hace SELECT+INSERT+COMMIT por fila,
    inviable contra una base remota.
  - año que ya tiene datos -> camino con dedup (`insert_if_changed`), más lento pero idempotente.

Así una corrida cortada a la mitad se reanuda rápido (los años que faltan siguen yendo por bulk)
y correr `run` antes que `load-history` no condena al backfill entero al camino lento. Re-correrlo
siempre es seguro.

Flags:
  --desde AAAA / --hasta AAAA   acotar el rango de años (default: 2005 .. año actual)
  --pausa SEG                   espera entre requests (default 0.3; son ~1.100 páginas)
  --force                       re-insertar aunque no haya cambiado (sólo en el camino con dedup)
"""
from __future__ import annotations

import argparse
import datetime as dt
import time

import requests
import urllib3

from etl.core import db, report
from . import config, source

COLS = ["cultivo", "cosecha", "sector", "metrica", "date", "valor", "corte", "estado", "fuente"]
# 3 segundos entre páginas. La cuenta, no una corazonada: el bloqueo del 02/08/2026 se disparó con
# ~700 páginas a 0.15s de pausa, que con el fetch real son ~1.8 requests/segundo sostenidos. A 3s
# el ritmo baja a ~0.3 req/s, un orden de magnitud por debajo. La carga completa (~1.100 páginas)
# tarda ~1 hora; el bloqueo que evita duró 4 y se lleva puestos los otros 4 ETLs que salen de esta
# misma IP contra el mismo host (granos, aves, bovinos, leche). La asimetría es tan grande que no
# hay ritmo "razonable" que justifique apurar esto: es un one-off.
PAUSA_DEFAULT = 3.0


def _anio_cargado(conn, anio: int) -> bool:
    """True si la tabla ya tiene alguna fila de ese año."""
    with conn.cursor() as cur:
        cur.execute(f"select 1 from {config.TABLE} where date >= %s and date <= %s limit 1",
                    (dt.date(anio, 1, 1), dt.date(anio, 12, 31)))
        return cur.fetchone() is not None


def _fechas_del_anio(anio: int, rep: report.Report) -> list[dt.date]:
    """Semanas publicadas en el índice de un año. [] si el índice no se pudo leer.

    Se scrapea el índice en vez de generar los miércoles: no todas las semanas se publican y un
    404 por fecha inventada sería indistinguible de una caída de la fuente.
    """
    try:
        html = source.fetch_html(source.url_anio(anio))
    except Exception as e:  # noqa: BLE001 - cualquier falla de red/HTTP es una falla de la corrida
        rep.error(f"indice {anio}: {e}")
        return []
    fechas = [f for f in source.parse_indice_anual(html) if f.year == anio]
    if not fechas:
        rep.error(f"indice {anio}: no se encontró ninguna semana")
    return fechas


def _bajar_semana(fecha: dt.date, rep: report.Report) -> tuple[list[dict], str] | None:
    """Filas de una semana, o None si no hay nada que cargar (ya reportado)."""
    url = source.url_semana(fecha)
    try:
        html = source.fetch_html(url)
    except requests.HTTPError as e:
        # El índice linkea semanas cuya página no existe (visto un 404 en 2020). Es un agujero de
        # la fuente, no una falla del ETL.
        if e.response is not None and e.response.status_code == 404:
            rep.note(f"{fecha}", "pagina inexistente (404)")
            return None
        rep.note(f"{fecha}", f"error HTTP: {e}", failure=True)
        return None
    except Exception as e:  # noqa: BLE001
        rep.note(f"{fecha}", f"error bajando: {e}", failure=True)
        return None

    try:
        return source.parse_semana(html, fecha), url
    except source.SemanaSinDatos:
        rep.note(f"{fecha}", "la fuente avisa que esta semana se acumuló en otra")
        return None
    except source.FormatoDesconocido as e:
        # Formato nuevo o parser roto: esto SÍ es una falla, hay que mirarlo.
        rep.note(f"{fecha}", f"no parsea: {e}", failure=True)
        return None


def main(argv=None) -> None:
    hoy = dt.date.today()
    ap = argparse.ArgumentParser(prog="etl compras_granos load-history",
                                 description="Carga histórica semanal 2005 -> hoy (one-off).")
    ap.add_argument("--desde", type=int, default=config.START_YEAR, metavar="AAAA",
                    help=f"primer año a cargar (default: {config.START_YEAR})")
    ap.add_argument("--hasta", type=int, default=hoy.year, metavar="AAAA",
                    help="último año a cargar (default: año actual)")
    ap.add_argument("--pausa", type=float, default=PAUSA_DEFAULT, metavar="SEG",
                    help=f"espera entre requests (default: {PAUSA_DEFAULT})")
    ap.add_argument("--force", action="store_true",
                    help="re-insertar aunque no cambie (sólo con la tabla ya poblada)")
    args = ap.parse_args(argv)
    urllib3.disable_warnings()

    rep = report.Report("compras_granos", "load-history")
    conn = db.get_conn()
    try:
        rep.info(f"años {args.desde}..{args.hasta}")

        for anio in range(args.desde, args.hasta + 1):
            # Un año sin nada cargado no tiene contra qué deduplicar: va por bulk.
            masivo = not _anio_cargado(conn, anio)
            fechas = _fechas_del_anio(anio, rep)
            if not fechas:
                continue
            lote: list[tuple] = []
            semanas = 0
            for fecha in fechas:
                res = _bajar_semana(fecha, rep)
                time.sleep(args.pausa)
                if res is None:
                    continue
                filas, url = res
                semanas += 1
                for f in filas:
                    if masivo:
                        lote.append((f["cultivo"], f["cosecha"], f["sector"], f["metrica"],
                                     f["date"], f["valor"], f["corte"], config.ESTADO, url))
                    else:
                        rep.tally(db.insert_if_changed(
                            conn, table=config.TABLE, key_cols=config.KEY_COLS,
                            key_vals=[f["cultivo"], f["cosecha"], f["sector"], f["metrica"],
                                      f["date"]],
                            value_cols=config.VALUE_COLS, row={"valor": f["valor"]},
                            estado=config.ESTADO, fuente=url, force=args.force,
                            extra={"corte": f["corte"]},
                        ))
            if masivo and lote:
                # Una transacción por año: acota la memoria y deja el avance commiteado, así una
                # corrida cortada a la mitad no pierde los años ya cargados.
                n = db.bulk_insert(conn, table=config.TABLE, cols=COLS, rows=lote)
                for _ in range(n):
                    rep.tally("nuevo")
            modo = "bulk" if masivo else "dedup"
            rep.info(f"{anio}: semanas={semanas}/{len(fechas)}  modo={modo}"
                     + (f"  filas={len(lote)}" if masivo else ""))
    finally:
        conn.close()
    rep.summary()


if __name__ == "__main__":
    main()
