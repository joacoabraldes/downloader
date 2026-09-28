"""ETL incremental del Índice de Confianza en el Gobierno (UTDT -> Postgres).

Dos cargas independientes en la misma corrida, ambas con estado='definitivo':

  1. Serie principal `icg`: baja la planilla de UTDT (resolviendo el link en cada corrida, ver
     `etl.core.utdt`), parsea la serie completa y snapshotea cada mes.
  2. Aperturas (componentes y cortes demográficos, ver `config.APERTURAS`): baja los
     microdatos .dta de la misma página y calcula cada serie como media ponderada por mes
     (`source_microdatos`). `fuente` = URL del .dta.

`insert_if_changed` absorbe las revisiones que UTDT hace sobre meses ya publicados.

Una falla en los microdatos NO impide cargar la serie principal (ni al revés): cada bloque
registra su propio `rep.error()` y la corrida sale con código != 0, pero lo que sí se pudo
bajar queda cargado.

Control de integridad
---------------------
El ICG total recalculado desde los microdatos se compara con la planilla, mes a mes (NO se
carga como serie: la principal es siempre la de la planilla). Coinciden en 289 de 299 meses
con `config.TOLERANCIA_CONTROL`; los 10 restantes (2008-03, 2008-05, 2011-01, 2011-06,
2011-11, 2012-07, 2012-11, 2014-03, 2017-06, 2023-08; hasta 0.047) son diferencias históricas
entre las dos publicaciones de UTDT, estables entre corridas. Por eso:

  - una diferencia en un mes viejo se informa (`rep.info`) sin marcar falla;
  - una diferencia en el ÚLTIMO mes de la planilla es `rep.error`: es el mes que esta corrida
    existe para traer, y un desvío ahí indica un .dta roto o un cambio de metodología;
  - que el .dta no traiga el último mes de la planilla también es `rep.error` (las dos se
    suben juntas; si no, las aperturas quedarían un mes atrás sin que nadie se entere).

No hay `load_history`: la planilla y el .dta SON el histórico completo (2001-11 →), así que
la primera corrida carga todo y las siguientes sólo agregan el mes nuevo.

El ICG no se desestacionaliza (ver la nota en etl/series_desest.toml).

Flags (aplican a las dos cargas):
  --force          insertar snapshot aunque no haya cambiado
  --desde YYYY-MM  ignorar los meses anteriores (por defecto se carga todo)
"""
from __future__ import annotations

import argparse
import datetime as dt

from etl.core import db, report
from . import config, source, source_microdatos

# Tolerancia del dedup (la misma que el default de `db.insert_if_changed`).
TOL = 1e-6


def _mes(texto: str) -> dt.date:
    y, m = map(int, texto.split("-"))
    return dt.date(y, m, 1)


def _vigentes(conn) -> dict[tuple[str, dt.date], float]:
    """{(serie, mes): valor} del último snapshot observado de cada (serie, mes).

    Mismo criterio que la vista `etl_icg_actual`: todo lo que no sea la desestacionalizada.

    Una sola query en vez del SELECT por fila de `insert_if_changed`: con 17 series x ~300
    meses son ~5100 roundtrips por corrida (~7 min contra la base remota) para, casi siempre,
    no insertar nada. Con esto sólo se llama a `insert_if_changed` para lo que falta o cambió
    (el mes nuevo, o una revisión de UTDT), así que el dedup sigue siendo el del núcleo.
    """
    with conn.cursor() as cur:
        cur.execute(f"select distinct on (serie, date) serie, date, valor from {config.TABLE} "
                    f"where estado is distinct from 'desestacionalizado' "
                    f"order by serie, date, ingested_at desc")
        return {(s, d): v for s, d, v in cur.fetchall()}


def _cargar(conn, rep, serie: str, datos: dict[dt.date, float], url: str, args,
            vigentes: dict[tuple[str, dt.date], float]) -> int:
    """Snapshotea los meses de una serie (filtrando por --desde). Devuelve cuántos leyó."""
    meses = [f for f in sorted(datos) if args.desde is None or f >= args.desde]
    for fecha in meses:
        valor = float(datos[fecha])
        previo = vigentes.get((serie, fecha))
        if not args.force and previo is not None and abs(previo - valor) <= TOL:
            rep.tally("sin_cambios")
            continue
        rep.tally(db.insert_if_changed(
            conn, table=config.TABLE, key_cols=config.KEY_COLS,
            key_vals=[serie, fecha], value_cols=config.VALUE_COLS,
            row={"valor": valor}, estado="definitivo", fuente=url, force=args.force,
            tol=TOL,
        ))
    return len(meses)


def _principal(conn, rep, args, vigentes) -> dict[dt.date, float] | None:
    """Carga `icg` desde la planilla. Devuelve la serie completa (para el control) o None."""
    try:
        datos, url = source.get_serie()
    except Exception as e:
        rep.error(f"planilla: bajando/parseando: {e}")
        return None
    if not datos:
        rep.error("planilla: no trajo ningún mes")
        return None
    meses = sorted(datos)
    rep.info(f"planilla: {url} | meses: {meses[0]:%Y-%m}..{meses[-1]:%Y-%m}")
    if not _cargar(conn, rep, config.MAIN_SERIE, datos, url, args, vigentes):
        rep.error(f"planilla: ningún mes desde {args.desde:%Y-%m}")
    return datos


def _controlar(rep, planilla: dict[dt.date, float], total: dict[dt.date, float]) -> None:
    """ICG total de los microdatos vs. la planilla (ver docstring del módulo)."""
    ultimo = max(planilla)
    if ultimo not in total:
        rep.error(f"microdatos: el .dta no trae {ultimo:%Y-%m}, que la planilla ya publicó")
    difs = {m: total[m] - v for m, v in planilla.items()
            if m in total and abs(total[m] - v) > config.TOLERANCIA_CONTROL}
    comparados = sum(1 for m in planilla if m in total)
    if ultimo in difs:
        rep.error(f"control: el ICG de {ultimo:%Y-%m} recalculado desde los microdatos "
                  f"({total[ultimo]:.3f}) no coincide con la planilla ({planilla[ultimo]:.3f})")
    viejos = {m: d for m, d in difs.items() if m != ultimo}
    detalle = ", ".join(f"{m:%Y-%m} ({d:+.3f})" for m, d in sorted(viejos.items()))
    rep.info(f"control microdatos vs planilla: {comparados - len(difs)}/{comparados} meses "
             f"dentro de ±{config.TOLERANCIA_CONTROL}"
             + (f"; diferencias históricas: {detalle}" if viejos else ""))


def _aperturas(conn, rep, args, vigentes, planilla: dict[dt.date, float] | None) -> None:
    """Carga las aperturas desde los microdatos, con el control contra la planilla."""
    try:
        m = source_microdatos.get_aperturas()
    except Exception as e:
        rep.error(f"microdatos: bajando/calculando: {e}")
        return
    meses = sorted(m.total)
    rep.info(f"microdatos: {m.url} | meses: {meses[0]:%Y-%m}..{meses[-1]:%Y-%m}")
    for aviso in m.avisos:
        rep.info(f"microdatos: aviso: {aviso}")
    if m.descartadas:
        detalle = ", ".join(f"{s} {f:%Y-%m} ({n})" for s, f, n in m.descartadas)
        rep.info(f"microdatos: celdas con menos de {config.MIN_CASOS} casos, no se cargan: "
                 f"{detalle}")
    if planilla:
        _controlar(rep, planilla, m.total)
    else:
        rep.info("control microdatos vs planilla: omitido (no hay planilla en esta corrida)")
    for serie in config.APERTURAS:
        _cargar(conn, rep, serie, m.aperturas[serie], m.url, args, vigentes)


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="etl icg",
                                 description="ETL del ICG de UTDT (serie principal + aperturas)")
    ap.add_argument("--force", action="store_true", help="insertar aunque no cambie")
    ap.add_argument("--desde", metavar="YYYY-MM", type=_mes,
                    help="cargar sólo desde ese mes (default: todo)")
    args = ap.parse_args(argv)

    rep = report.Report("icg", "run")
    conn = db.get_conn()
    try:
        vigentes = _vigentes(conn)
        planilla = _principal(conn, rep, args, vigentes)
        _aperturas(conn, rep, args, vigentes, planilla)
        rep.summary()
    finally:
        conn.close()


if __name__ == "__main__":
    main()
