"""ETL de estimaciones agrícolas por departamento (MAGyP). Backfill e incremental son lo mismo.

La fuente publica la base ENTERA en cada release (no hay incremental posible), así que cada
corrida hace, como mucho, DOS requests contra un host que comparten otros 5 ETLs desde esta IP:

  1. GET de la página -> "Fecha de Actualización". Si ese release ya está en
     `etl_estimaciones_agricolas_releases`, termina acá (la corrida normal de todas las semanas).
  2. Pausa y POST del formulario -> CSV completo (~11 MB). Una sola vez, sin reintentos.

Carga (append-only, dedup por (clave, estado) como en todo el repo):
  - Se lee UNA vez el último snapshot de todas las claves y se compara en memoria. Con ~35 ms de
    ida y vuelta a la base, pasar las ~160 mil filas por `insert_if_changed` (SELECT + INSERT +
    COMMIT por fila) serían horas por release.
  - Clave sin snapshot previo -> `bulk_insert` (no hay contra qué deduplicar; mismo criterio que
    el load-history de compras_granos). Es el backfill entero y las campañas nuevas.
  - Clave que cambió -> `insert_if_changed`, que vuelve a comparar contra la base antes de
    escribir. Son las revisiones de MAGyP: del orden de cien celdas por release.
  - Al final se registra el release. Si algo falla antes, no se registra y la próxima corrida
    lo reintenta.

Flags:
  --archivo PATH   leer el CSV de un archivo local en vez de hacer el POST (backfill, pruebas)
  --fecha AAAA-MM-DD  fecha del release a registrar (evita el GET; útil con --archivo)
  --force          procesar aunque el release ya esté registrado (igual deduplica: no fuerza
                   snapshots, sólo vuelve a bajar y comparar)
"""
from __future__ import annotations

import argparse
import datetime as dt
import time
from pathlib import Path

from etl.core import db, report
from . import config, source

PAUSA = 5.0          # segundos entre el GET y el POST (host MAGyP compartido: >= 4 s)
MIN_FILAS = 150_000  # el release del 25/08/2026 trae 162.041: menos es un CSV cortado


def _release_registrado(conn, fecha: dt.date) -> bool:
    with conn.cursor() as cur:
        cur.execute(f"select 1 from {config.RELEASES_TABLE} where fecha_actualizacion = %s",
                    (fecha,))
        return cur.fetchone() is not None


def _registrar_release(conn, fecha: dt.date, filas: int, nuevos: int, actualizados: int) -> None:
    with conn.cursor() as cur:
        cur.execute(
            f"insert into {config.RELEASES_TABLE} "
            f"(fecha_actualizacion, filas, nuevos, actualizados, fuente) values (%s,%s,%s,%s,%s) "
            f"on conflict (fecha_actualizacion) do nothing",
            (fecha, filas, nuevos, actualizados, source.URL),
        )
    conn.commit()


def _snapshots(conn) -> dict[tuple, dict]:
    """Último snapshot de cada clave: {(cultivo, campania, departamento_id): {col: valor}}."""
    keys, vals = config.KEY_COLS, config.VALUE_COLS
    sql = (f"select distinct on ({', '.join(keys)}) {', '.join(keys + vals)} "
           f"from {config.TABLE} where estado = %s "
           f"order by {', '.join(keys)}, ingested_at desc")
    out: dict[tuple, dict] = {}
    with conn.cursor() as cur:
        cur.execute(sql, (config.ESTADO,))
        for r in cur:
            out[tuple(r[:len(keys)])] = {c: (float(v) if v is not None else None)
                                         for c, v in zip(vals, r[len(keys):])}
    return out


def _mapear(filas: list[dict], rep: report.Report) -> list[dict] | None:
    """Agrega el slug `cultivo` y valida la clave. None si hay que abortar (ya reportado)."""
    desconocidos = sorted({f["id_cultivo_fuente"] for f in filas} - set(config.CULTIVOS))
    if desconocidos:
        rep.error(f"ids de cultivo sin mapear en config.CULTIVOS: {desconocidos}")
        return None
    vistas: set[tuple] = set()
    for f in filas:
        f["cultivo"] = config.CULTIVOS[f["id_cultivo_fuente"]]
        clave = (f["cultivo"], f["campania"], f["departamento_id"])
        if clave in vistas:
            rep.error(f"clave duplicada en el CSV: {clave}")
            return None
        vistas.add(clave)
    return filas


def cargar(conn, filas: list[dict], fecha: dt.date,
           rep: report.Report) -> tuple[int, int]:
    """Compara el release contra la base e inserta lo nuevo / cambiado. Devuelve (nuevos, act.)."""
    previos = _snapshots(conn)
    rep.info(f"claves en la base: {len(previos)}")
    cols = (config.KEY_COLS + config.VALUE_COLS + config.EXTRA_COLS + ["estado", "fuente"])
    lote: list[tuple] = []
    actualizados = 0
    for f in filas:
        if f[config.VALUE_COLS[0]] is None:  # mismo criterio que insert_if_changed
            rep.tally("saltado")
            continue
        clave = tuple(f[c] for c in config.KEY_COLS)
        f["fecha_actualizacion"] = fecha
        extra = {c: f[c] for c in config.EXTRA_COLS}
        prev = previos.get(clave)
        if prev is None:
            lote.append(tuple(f[c] for c in config.KEY_COLS + config.VALUE_COLS)
                        + tuple(extra.values()) + (config.ESTADO, source.URL))
            continue
        # Mismo criterio y tolerancia que insert_if_changed, que igual re-compara contra la base.
        if not db._changed(prev, f, config.VALUE_COLS, 1e-6):
            rep.tally("sin_cambios")
            continue
        status = db.insert_if_changed(
            conn, table=config.TABLE, key_cols=config.KEY_COLS, key_vals=list(clave),
            value_cols=config.VALUE_COLS, row=f, estado=config.ESTADO, fuente=source.URL,
            extra=extra,
        )
        rep.tally(status)
        actualizados += status == "actualizado"
    nuevos = db.bulk_insert(conn, table=config.TABLE, cols=cols, rows=lote)
    for _ in range(nuevos):
        rep.tally("nuevo")
    _desaparecidas(previos, filas, rep)
    return nuevos, actualizados


def _desaparecidas(previos: dict[tuple, dict], filas: list[dict], rep: report.Report) -> None:
    """Claves con snapshot previo que el release (base COMPLETA) ya no trae. Sólo se informa:
    la fuente puede dar de baja celdas legítimamente, y la tabla es append-only (la última fila
    sigue vigente en `_actual`). Un salto grande acá es la pista de un release recortado."""
    presentes = {tuple(f[c] for c in config.KEY_COLS) for f in filas}
    faltan = sorted(set(previos) - presentes, key=str)
    if faltan:
        rep.sumar("desaparecidas", len(faltan))
        rep.info(f"claves previas ausentes en este release: {len(faltan)} "
                 f"(ej.: {faltan[:5]})")


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="etl estimaciones_agricolas",
                                 description="Estimaciones agrícolas por departamento (MAGyP).")
    ap.add_argument("--archivo", type=Path, metavar="PATH",
                    help="CSV local del portal en vez de hacer el POST")
    ap.add_argument("--fecha", metavar="AAAA-MM-DD",
                    help="fecha del release (evita el GET de la página)")
    ap.add_argument("--force", action="store_true",
                    help="procesar aunque el release ya esté registrado")
    args = ap.parse_args(argv)

    rep = report.Report("estimaciones_agricolas", "run")
    conn = db.get_conn()
    try:
        if args.fecha:
            fecha = dt.date.fromisoformat(args.fecha)
        else:
            try:
                fecha = source.parse_fecha_actualizacion(source.fetch_pagina())
            except Exception as e:  # noqa: BLE001 - fuente caída o página cambiada
                rep.error(f"leyendo la fecha de actualización: {e}")
                return
        if _release_registrado(conn, fecha):
            if not args.force:
                rep.info(f"release {fecha} ya procesado: sin release nuevo")
                return
            rep.info(f"release {fecha} ya procesado; --force: se vuelve a comparar")
        else:
            rep.info(f"release nuevo: {fecha}")

        try:
            if args.archivo:
                raw = args.archivo.read_bytes()
            else:
                if not args.fecha:
                    time.sleep(PAUSA)
                raw = source.fetch_csv()
            filas, descartes = source.parse_csv(raw)
        except Exception as e:  # noqa: BLE001 - fuente caída o formato nuevo: falla de corrida
            rep.error(f"bajando/parseando el CSV: {e}")
            return
        if len(filas) < MIN_FILAS:
            rep.error(f"CSV con {len(filas)} filas (< {MIN_FILAS}): ¿respuesta cortada?")
            return
        filas = _mapear(filas, rep)
        if filas is None:
            return
        campanias = sorted({f["campania"] for f in filas})
        rep.info(f"filas={len(filas)}  descartadas_sin_geo={descartes['sin_geo']}  "
                 f"cultivos={len({f['cultivo'] for f in filas})}  "
                 f"campañas={campanias[0]}..{campanias[-1]}")

        nuevos, actualizados = cargar(conn, filas, fecha, rep)
        _registrar_release(conn, fecha, len(filas), nuevos, actualizados)
    finally:
        conn.close()
        rep.summary()


if __name__ == "__main__":
    main()
