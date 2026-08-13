"""ETL incremental de las series de la API oficial del Estado (apis.datos.gob.ar -> Postgres).

Baja una serie por request, valida las fechas y snapshotea cada (serie, mes) con
estado='definitivo'. `insert_if_changed` absorbe las revisiones de los organismos.

No hay `load_history`: la API devuelve la serie completa, así que la primera corrida carga todo
el histórico y las siguientes sólo agregan el período nuevo.

Las series se guardan tal como las publica el organismo. Sobre eso, la corrida agrega X-13 para
las dos series de ventas, y sobre su serie REAL, no la nominal (ver la nota en
etl/series_desest.toml).

Sumar una serie = agregar una fila a `SERIES_META` en config.py. No hay que tocar este archivo.

Flags:
  --force          insertar snapshot aunque no haya cambiado
  --serie NOMBRE   correr sólo esa serie (repetible)
  --desde YYYY-MM  ignorar los meses anteriores
"""
from __future__ import annotations

import argparse
import datetime as dt

from etl.core import db, desest_params, report, seasonal
from . import config, source


def _mes(texto: str) -> dt.date:
    y, m = map(int, texto.split("-"))
    return dt.date(y, m, 1)


def sincronizar_dimension(conn) -> None:
    """Upsert de `etl_datos_gob_series` desde config.SERIES_META (única fuente de verdad)."""
    filas = [(serie, meta[0], meta[1], meta[2], meta[3], meta[4], i,
              config.REAL_DESDE.get(serie))
             for i, (serie, meta) in enumerate(config.SERIES_META.items(), start=1)]
    with conn.cursor() as cur:
        cur.executemany(
            """insert into etl_datos_gob_series
                 (serie, id_api, nombre, unidad, organismo, deflactable, orden, real_desde)
               values (%s, %s, %s, %s, %s, %s, %s, %s)
               on conflict (serie) do update set
                 id_api = excluded.id_api, nombre = excluded.nombre, unidad = excluded.unidad,
                 organismo = excluded.organismo, deflactable = excluded.deflactable,
                 orden = excluded.orden, real_desde = excluded.real_desde""",
            filas)
    conn.commit()


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="etl datos_gob",
                                 description="ETL de la API oficial de series de tiempo")
    ap.add_argument("--force", action="store_true", help="insertar aunque no cambie")
    ap.add_argument("--serie", action="append", metavar="NOMBRE",
                    help="correr sólo esa serie (repetible)")
    ap.add_argument("--desde", metavar="YYYY-MM", type=_mes,
                    help="cargar sólo desde ese mes (default: toda la serie)")
    ap.add_argument("--no-desest", action="store_true",
                    help="saltear la desestacionalización X-13")
    ap.add_argument("--x13-out", metavar="DIR",
                    help="guardar la salida de X-13 (html/factores/diagnósticos) en DIR")
    args = ap.parse_args(argv)

    series = args.serie or config.SERIES
    desconocidas = [s for s in series if s not in config.SERIES_META]
    if desconocidas:
        ap.error(f"serie(s) desconocida(s): {', '.join(desconocidas)}. "
                 f"Opciones: {', '.join(config.SERIES)}")

    rep = report.Report("datos_gob", "run")
    rep.info(f"fuente: {source.BASE} | series: {len(series)}")
    conn = db.get_conn()
    try:
        sincronizar_dimension(conn)
        for serie in series:
            serie_id = config.SERIES_META[serie][0]
            try:
                filas, descartes = source.get_serie(serie_id)
            except Exception as e:
                rep.note(serie, f"ERROR bajando {serie_id}: {e}", status="saltado", failure=True)
                continue
            for d in descartes:
                # Un dato tirado se avisa: es una anomalía de la fuente, no ruido nuestro.
                rep.info(f"{serie}: descartado -> {d}")
            if args.desde:
                filas = [(f, v) for f, v in filas if f >= args.desde]
            if not filas:
                rep.note(serie, "sin datos", status="no_publicado")
                continue
            for fecha, valor in filas:
                rep.tally(db.insert_if_changed(
                    conn, table=config.TABLE, key_cols=config.KEY_COLS,
                    key_vals=[serie, fecha], value_cols=config.VALUE_COLS,
                    row={"valor": valor}, estado="definitivo",
                    fuente=source.url_serie(serie_id), force=args.force,
                ))
            rep.info(f"{serie:24} {len(filas):>4} meses  "
                     f"{filas[0][0]:%Y-%m}..{filas[-1][0]:%Y-%m}  ult={filas[-1][1]:g}")
        rep.summary()

        # X-13 sobre la serie REAL (ver la clave `view` en etl/series_desest.toml). A diferencia
        # del resto del repo NO se condiciona a `rep.changed`: el insumo es la serie deflactada,
        # así que un mes nuevo del IPC cambia toda la serie real aunque las ventas no se hayan
        # movido, y la desest quedaría vieja.
        if args.no_desest:
            pass
        else:
            seasonal.run_desest(conn, "datos_gob",
                                desest_params.build_jobs("datos_gob", keep_dir=args.x13_out))
    finally:
        conn.close()


if __name__ == "__main__":
    main()
