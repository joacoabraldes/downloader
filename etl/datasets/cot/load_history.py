"""Carga histórica (one-off) del Commitments of Traders: zips anuales de la CFTC.

Existe porque la API Socrata NO tiene todo el histórico: legacy arranca en 1998-01-06 y
disaggregated en 2006-06-13. El tramo **1986-1997 de non_commercial vive sólo en los zips**, y
son justamente los doce años que hacen que la serie larga valga la pena.

Por cada uno de los cuatro orígenes se baja el archivo multianual (todo hasta 2016) más un
anual por cada año de 2017 en adelante: unos 44 archivos, ~200 MB. No hay que espaciar los
requests como con MAGyP —es un sitio del gobierno de EEUU detrás de CDN, sin corte por volumen—
pero igual la pausa es configurable por si acaso.

Un archivo que no existe (el anual del año en curso antes de la primera publicación) se saltea
con un aviso, no hace fallar la corrida. Los archivos multianuales y los anuales SE PISAN en los
años que comparten, así que la carga se guía por las claves ya vistas, no por el archivo.

Por defecto las filas ya cargadas se saltean: releer 40 años fila por fila para cazar una
revisión de la CFTC cuesta horas contra una base remota, y de las revisiones recientes ya se
ocupa `run`. Para forzar esa relectura está `--revisar`.

Flags:
  --origen CAT/TIPO   cargar sólo un origen (p.ej. non_commercial/futures)
  --desde-anio AAAA   primer año anual a bajar (default: 2017)
  --sin-hist          saltear los archivos multianuales (sólo los anuales)
  --revisar           releer las filas ya cargadas para cazar revisiones de la CFTC (lento)
  --pausa SEG         espera entre descargas (default: 1.0)
  --force             re-insertar aunque no cambie
"""
from __future__ import annotations

import argparse
import datetime as dt
import time

import requests

from etl.core import db, report
from . import config, source

COLS = ["contrato", "categoria", "tipo", "date",
        "largo", "corto", "spreading", "interes_abierto",
        "codigo_cftc", "unidad", "estado", "fuente"]
PAUSA_DEFAULT = 1.0


def _claves_cargadas(conn, categoria: str, tipo: str) -> set:
    """Claves (contrato, fecha) que ese origen ya tiene, en UNA query.

    Reemplaza al "¿el origen está vacío?" que usan los otros load-history del repo. Ahí la
    decisión es todo o nada: si hay UNA sola fila, el backfill entero cae en el camino con
    dedup, que hace SELECT+INSERT+COMMIT por fila. Acá eso pasa siempre, porque basta una
    corrida de `run` —4 requests de nada— para dejar cargadas las últimas semanas y condenar a
    las ~6.000 filas del histórico al camino lento: más de una hora contra una base remota.
    Con el set de claves, cada fila decide sola si es nueva (va a bulk) o si ya existe.
    """
    with conn.cursor() as cur:
        cur.execute(f"select contrato, date from {config.TABLE} "
                    f"where categoria = %s and tipo = %s", (categoria, tipo))
        return set(cur.fetchall())


def _bajar(nombre: str, categoria: str, tipo: str, layout: str, rep) -> list[dict]:
    """Filas de un zip. [] si el archivo no existe todavía o si falló (ya reportado)."""
    try:
        return source.bajar_zip(nombre, categoria, tipo, layout)
    except requests.HTTPError as e:
        # El anual del año en curso no existe hasta la primera publicación del año.
        if e.response is not None and e.response.status_code == 404:
            rep.note(nombre, "el archivo todavía no existe (404)")
            return []
        rep.note(nombre, f"error HTTP: {e}", failure=True)
        return []
    except source.FormatoDesconocido as e:
        rep.note(nombre, f"formato inesperado: {e}", failure=True)
        return []
    except Exception as e:  # noqa: BLE001
        rep.note(nombre, f"error bajando: {e}", failure=True)
        return []


def main(argv=None) -> None:
    hoy = dt.date.today()
    ap = argparse.ArgumentParser(prog="etl cot load-history",
                                 description="Carga histórica del COT desde los zips de la CFTC.")
    ap.add_argument("--origen", metavar="CAT/TIPO",
                    help="cargar sólo un origen, p.ej. non_commercial/futures")
    ap.add_argument("--desde-anio", type=int, default=config.PRIMER_ANIO_ANUAL, metavar="AAAA",
                    help=f"primer año anual (default: {config.PRIMER_ANIO_ANUAL})")
    ap.add_argument("--sin-hist", action="store_true",
                    help="saltear los archivos multianuales")
    ap.add_argument("--pausa", type=float, default=PAUSA_DEFAULT, metavar="SEG",
                    help=f"espera entre descargas (default: {PAUSA_DEFAULT})")
    ap.add_argument("--revisar", action="store_true",
                    help="releer las filas ya cargadas para cazar revisiones de la CFTC (lento)")
    ap.add_argument("--force", action="store_true", help="re-insertar aunque no cambie")
    args = ap.parse_args(argv)

    origenes = config.ORIGENES
    if args.origen:
        clave = tuple(args.origen.split("/", 1))
        if clave not in config.ORIGENES:
            ap.error(f"origen desconocido: {args.origen}. "
                     f"Opciones: {', '.join('/'.join(k) for k in config.ORIGENES)}")
        origenes = {clave: config.ORIGENES[clave]}

    rep = report.Report("cot", "load-history")
    conn = db.get_conn()
    try:
        for (categoria, tipo), o in origenes.items():
            cargadas = _claves_cargadas(conn, categoria, tipo)
            archivos = ([] if args.sin_hist else [o["hist"]]) + [
                o["anual"].format(anio=a) for a in range(args.desde_anio, hoy.year + 1)]
            lote: list[tuple] = []
            leidas = ya = 0
            for nombre in archivos:
                filas = _bajar(nombre, categoria, tipo, o["layout"], rep)
                time.sleep(args.pausa)
                leidas += len(filas)
                for f in filas:
                    if (f["contrato"], f["date"]) not in cargadas:
                        lote.append((f["contrato"], f["categoria"], f["tipo"], f["date"],
                                     f["largo"], f["corto"], f["spreading"],
                                     f["interes_abierto"], f["codigo_cftc"], f["unidad"],
                                     config.ESTADO, f["fuente"]))
                        # El multianual y el anual se pisan en los años que comparten.
                        cargadas.add((f["contrato"], f["date"]))
                    elif args.revisar:
                        # Sólo bajo pedido: releer 40 años fila por fila para cazar una revisión
                        # cuesta horas, y de las revisiones recientes ya se ocupa `run`.
                        ya += 1
                        rep.tally(db.insert_if_changed(
                            conn, table=config.TABLE, key_cols=config.KEY_COLS,
                            key_vals=[f["contrato"], f["categoria"], f["tipo"], f["date"]],
                            value_cols=config.VALUE_COLS,
                            row={c: f[c] for c in config.VALUE_COLS},
                            estado=config.ESTADO, fuente=f["fuente"], force=args.force,
                            extra={"codigo_cftc": f["codigo_cftc"], "unidad": f["unidad"]},
                        ))
                    else:
                        ya += 1
            if lote:
                n = db.bulk_insert(conn, table=config.TABLE, cols=COLS, rows=lote)
                for _ in range(n):
                    rep.tally("nuevo")
            rep.info(f"{categoria}/{tipo}: {len(archivos)} archivos, {leidas} filas leidas, "
                     f"{len(lote)} nuevas, {ya} ya estaban"
                     + (" (revisadas)" if args.revisar else " (salteadas)"))
    finally:
        conn.close()
    rep.summary()


if __name__ == "__main__":
    main()
