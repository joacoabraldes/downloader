"""ETL incremental de los precios FOB oficiales de granos (MAGyP).

La API sirve un día por request, así que la corrida diaria baja la ventana que va desde el
último día cargado hasta hoy. Por defecto re-lee 7 días hacia atrás además de lo que falta:
la fuente publica circulares con efecto retroactivo y un día que hoy vino vacío puede tener
precio mañana. Como la tabla es append-only con dedup, releer es barato (no inserta nada si
no cambió) y evita agujeros permanentes.

Un día sin cotización (fin de semana, feriado, o la fuente que no publicó) NO es una falla:
la API devuelve una lista vacía y se cuenta como `no_publicado`. Sí es falla que la ventana
entera venga sin un solo día con datos: eso es la fuente caída.

Flags:
  --desde AAAA-MM-DD   primer día a bajar (default: último cargado - 7, o 30 días atrás)
  --hasta AAAA-MM-DD   último día a bajar (default: hoy)
  --dias N             atajo: bajar los últimos N días (ignora --desde)
  --pausa SEG          espera entre requests (default 1.0)
  --force              insertar snapshot aunque no haya cambiado
  --no-refresh         no refrescar las materializadas del PRP al terminar

Al final de cada corrida refresca `granos_prp` y `granos_prp_combinado`, que son MATERIALIZADAS.
Se refrescan siempre, haya datos nuevos o no: el PRP tambien depende de deflactores, A3500, dex,
vbp_granos y tc_granos, que cambian sin que este ETL corra.
"""
from __future__ import annotations

import argparse
import datetime as dt
import time

import urllib3

from etl.core import db, report
from . import config, source

# Materializadas que dependen de este dataset. El orden importa: granos_prp_combinado lee de
# granos_prp, asi que primero se refresca la base.
MATERIALIZADAS = ["granos_prp", "granos_prp_combinado"]


def refrescar(conn, rep) -> None:
    """Refresca las materializadas del PRP. CONCURRENTLY para no bloquear a quien este leyendo.

    Por que existe: la cadena de vistas del PRP tarda ~1,7 s por consulta y el 43% es
    `deflactores`, que no es de este repo y se escanea dos veces. Materializado baja a ~10 ms.
    El precio es que hay que refrescar, y se hace aca porque es el unico proceso que corre todos
    los dias. OJO: el PRP tambien depende de dex, vbp_granos y tc_granos, que mantiene un CRUD
    aparte -- despues de editar esas tablas hay que refrescar a mano o esperar a esta corrida.
    """
    for mv in MATERIALIZADAS:
        try:
            with conn.cursor() as cur:
                cur.execute(f"refresh materialized view concurrently {mv}")
            conn.commit()
            rep.info(f"refrescada {mv}")
        except Exception as e:  # noqa: BLE001
            conn.rollback()
            rep.note(mv, f"no se pudo refrescar: {e}", failure=True)

# Días hacia atrás que se re-leen además de lo que falta (circulares retroactivas).
SOLAPE = 7
# Ventana por defecto cuando la tabla está vacía: la carga histórica va por `load-history`.
VENTANA_INICIAL = 30
PAUSA_DEFAULT = 1.0


def main(argv=None) -> None:
    hoy = dt.date.today()
    ap = argparse.ArgumentParser(prog="etl fob_granos",
                                 description="ETL diario de precios FOB oficiales de granos (MAGyP)")
    ap.add_argument("--desde", type=dt.date.fromisoformat, metavar="AAAA-MM-DD",
                    help=f"primer día a bajar (default: último cargado - {SOLAPE})")
    ap.add_argument("--hasta", type=dt.date.fromisoformat, metavar="AAAA-MM-DD",
                    default=hoy, help="último día a bajar (default: hoy)")
    ap.add_argument("--dias", type=int, metavar="N",
                    help="bajar los últimos N días (ignora --desde)")
    ap.add_argument("--pausa", type=float, default=PAUSA_DEFAULT, metavar="SEG",
                    help=f"espera entre requests (default: {PAUSA_DEFAULT})")
    ap.add_argument("--no-refresh", action="store_true",
                    help="no refrescar las materializadas del PRP al terminar")
    ap.add_argument("--force", action="store_true", help="insertar aunque no cambie")
    args = ap.parse_args(argv)
    urllib3.disable_warnings()

    rep = report.Report("fob_granos", "run")
    conn = db.get_conn()
    try:
        if args.dias:
            desde = args.hasta - dt.timedelta(days=args.dias - 1)
        elif args.desde:
            desde = args.desde
        else:
            ultimo = db.last_date(conn, table=config.TABLE)
            desde = (ultimo - dt.timedelta(days=SOLAPE) if ultimo
                     else args.hasta - dt.timedelta(days=VENTANA_INICIAL))

        dias = list(source.dias_habiles(desde, args.hasta))
        if not dias:
            rep.info(f"ventana {desde}..{args.hasta}: ningún día hábil")
            rep.summary()
            return
        rep.info(f"ventana {desde}..{args.hasta} | dias habiles: {len(dias)}")

        con_datos = 0
        for i, fecha in enumerate(dias):
            try:
                filas, url = source.get_dia(fecha)
            except source.DiaSinCotizacion:
                # Se registra el día vacío para que la carga histórica no lo vuelva a pedir. El
                # incremental SÍ lo relee dentro de su ventana de solape: acá el interés es
                # captar la circular que llega tarde, no ahorrar el request.
                source.marcar_sin_dato(conn, fecha)
                rep.tally("no_publicado")
                continue
            except Exception as e:  # noqa: BLE001 - red/HTTP/parseo: falla de la corrida
                rep.note(f"{fecha}", f"error bajando: {e}", failure=True)
                continue
            finally:
                if i < len(dias) - 1:
                    time.sleep(args.pausa)
            con_datos += 1
            for f in filas:
                rep.tally(db.insert_if_changed(
                    conn, table=config.TABLE, key_cols=config.KEY_COLS,
                    key_vals=[f["producto"], f["date"], f["embarque_desde"], f["embarque_hasta"]],
                    value_cols=config.VALUE_COLS, row={"valor": f["valor"]},
                    estado=config.ESTADO, fuente=url, force=args.force,
                    extra={"posicion": f["posicion"], "circular": f["circular"]},
                ))
        # Que ningún día de la ventana traiga datos no es un feriado: es la fuente caída.
        if con_datos == 0:
            rep.error(f"ningún día de {desde}..{args.hasta} trajo precios")
        # Se refresca SIEMPRE, aunque no haya datos nuevos: el PRP tambien depende de
        # deflactores, A3500, dex, vbp_granos y tc_granos, que cambian sin que este ETL corra.
        if not args.no_refresh:
            refrescar(conn, rep)
        rep.summary()
    finally:
        conn.close()


if __name__ == "__main__":
    main()
