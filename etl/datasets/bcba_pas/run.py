"""ETL del Panorama Agrícola Semanal de la BCBA (tablero público de Power BI). Backfill = run.

Corrida normal (cron de los viernes): 2 requests (visor + modelsAndExploration). Si el
LastRefreshTime del modelo ya está en `etl_bcba_pas_releases`, termina ahí ("sin release nuevo").
Si no, 1 POST más con todo (Datos_al + dimensiones + Histórico_PAS), se compara contra el último
snapshot de cada clave y se escribe sólo lo nuevo o revisado.

El tablero guarda UNA sola foto por campaña y la pisa cada semana: la historia de vintages existe
sólo en esta tabla. Por eso la primera carga es la foto de ese día (no hay backfill de semanas
viejas posible por esta vía).

Carga (mismo esquema que estimaciones_agricolas / estimaciones_semanal): se lee una vez el último
snapshot de todas las claves, se compara en memoria (`db._changed`, tolerancia 1e-6), claves
nuevas por `bulk_insert`, cambiadas por `insert_if_changed`. Al final se registra el release. Si
algo falla antes (validación incluida), no se carga nada ni se registra: la próxima corrida
reintenta.

Flags:
  --force   procesar aunque el LastRefreshTime ya esté registrado (igual deduplica)
"""
from __future__ import annotations

import argparse
import datetime as dt

import requests

from etl.core import db, powerbi, report
from . import config, source


def _registrado(conn, last_refresh: dt.datetime) -> bool:
    with conn.cursor() as cur:
        cur.execute(f"select 1 from {config.RELEASES_TABLE} where last_refresh = %s",
                    (last_refresh,))
        return cur.fetchone() is not None


def _registrar(conn, rel: source.Release, d: source.Datos, nuevos: int,
               actualizados: int) -> None:
    with conn.cursor() as cur:
        cur.execute(
            f"insert into {config.RELEASES_TABLE} "
            f"(last_refresh, fecha_datos, modelo, celdas, filas, nuevos, actualizados) "
            f"values (%s,%s,%s,%s,%s,%s,%s) on conflict (last_refresh) do nothing",
            (rel.last_refresh, d.fecha_datos, rel.nombre_modelo, d.celdas, len(d.filas),
             nuevos, actualizados),
        )
    conn.commit()


def cargar(conn, d: source.Datos, rel: source.Release,
           rep: report.Report) -> tuple[int, int]:
    keys = config.KEY_COLS
    sql = (f"select distinct on ({', '.join(keys)}) {', '.join(keys)}, valor "
           f"from {config.TABLE} where estado = %s "
           f"order by {', '.join(keys)}, fecha_datos desc, ingested_at desc")
    previos: dict[tuple, dict] = {}
    with conn.cursor() as cur:
        cur.execute(sql, (config.ESTADO,))
        for r in cur:
            previos[tuple(r[:-1])] = {"valor": float(r[-1]) if r[-1] is not None else None}
    rep.info(f"claves en la base: {len(previos)}")

    cols = keys + config.VALUE_COLS + config.EXTRA_COLS + ["estado", "fuente"]
    lote: list[tuple] = []
    actualizados = 0
    for f in d.filas:
        clave = tuple(f[c] for c in keys)
        extra = {"zona": f["zona"], "fecha_datos": d.fecha_datos,
                 "last_refresh": rel.last_refresh}
        prev = previos.get(clave)
        if prev is None:
            lote.append(clave + (f["valor"],) + tuple(extra.values())
                        + (config.ESTADO, config.URL))
            continue
        if not db._changed(prev, f, config.VALUE_COLS, 1e-6):
            rep.tally("sin_cambios")
            continue
        status = db.insert_if_changed(
            conn, table=config.TABLE, key_cols=keys, key_vals=list(clave),
            value_cols=config.VALUE_COLS, row=f, estado=config.ESTADO, fuente=config.URL,
            extra=extra,
        )
        rep.tally(status)
        actualizados += status == "actualizado"
    nuevos = db.bulk_insert(conn, table=config.TABLE, cols=cols, rows=lote)
    for _ in range(nuevos):
        rep.tally("nuevo")
    _desaparecidas(previos, d.filas, rep)
    return nuevos, actualizados


def _desaparecidas(previos: dict[tuple, dict], filas: list[dict], rep: report.Report) -> None:
    """Claves con snapshot previo que este refresh (tabla de hechos COMPLETA) ya no trae. Sólo
    se informa: el tablero puede dar de baja celdas legítimamente (p.ej. una campaña que sale
    del histórico), y la tabla es append-only (la última fila sigue vigente en `_actual`)."""
    presentes = {tuple(f[c] for c in config.KEY_COLS) for f in filas}
    faltan = sorted(set(previos) - presentes, key=str)
    if faltan:
        rep.sumar("desaparecidas", len(faltan))
        rep.info(f"claves previas ausentes en este refresh: {len(faltan)} "
                 f"(ej.: {faltan[:5]})")


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="etl bcba_pas",
                                 description="Panorama Agrícola Semanal (BCBA, Power BI).")
    ap.add_argument("--force", action="store_true",
                    help="procesar aunque el LastRefreshTime ya esté registrado")
    args = ap.parse_args(argv)

    rep = report.Report("bcba_pas", "run")
    conn = db.get_conn()
    fuente = source.Fuente()
    try:
        try:
            rel = fuente.release()
        except requests.HTTPError as e:
            code = e.response.status_code if e.response is not None else "?"
            rep.error(f"el tablero no responde (HTTP {code}): ¿clave caída o reporte "
                      f"despublicado? {e}")
            return
        except (powerbi.PowerBIError, KeyError, ValueError) as e:
            rep.error(f"leyendo el modelo publicado: {e}")
            return
        rep.info(f"modelo '{rel.nombre_modelo}'  last_refresh={rel.last_refresh:%Y-%m-%d %H:%M}")
        if _registrado(conn, rel.last_refresh):
            if not args.force:
                rep.info("release ya procesado: sin release nuevo")
                return
            rep.info("release ya procesado; --force: se vuelve a comparar")

        try:
            d = fuente.datos()
        except (source.FormatoInesperado, powerbi.PowerBIError) as e:
            rep.error(f"validación del tablero: {e} (no se cargó nada)")
            return
        except requests.RequestException as e:
            rep.error(f"consultando el tablero: {e}")
            return
        campanias = sorted({f["campania"] for f in d.filas})
        rep.info(f"datos al {d.fecha_datos}  celdas={d.celdas}  filas={len(d.filas)}  "
                 f"campañas={campanias[0]}..{campanias[-1]}")
        nuevos, actualizados = cargar(conn, d, rel, rep)
        _registrar(conn, rel, d, nuevos, actualizados)
    finally:
        rep.info(f"requests={fuente.requests}")
        conn.close()
        rep.summary()


if __name__ == "__main__":
    main()
