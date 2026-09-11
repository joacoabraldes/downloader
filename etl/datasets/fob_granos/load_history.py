"""Carga histórica (one-off) de precios FOB oficiales de granos: 1993-01 -> hoy, día por día.

La API de MAGyP no tiene endpoint de rango: un request por fecha. El histórico completo son
~8.600 días hábiles, y ahí está todo el problema de este comando.

La pausa por defecto es 4 segundos, y sale de la cuenta, no de una corazonada. El bloqueo del
02/08/2026 (backfill de compras_granos) se disparó con ~1,8 requests/segundo sostenidos y duró
4 horas, tirando abajo el dominio entero para los CINCO ETLs que salen de esta misma IP contra
magyp.gob.ar (granos, aves, bovinos, leche, compras_granos). A 4 s el ritmo queda en ~0,22
req/s, un orden de magnitud por debajo, y la carga completa tarda ~10 horas. Es un one-off que
se corre una vez: no hay ritmo "razonable" que justifique apurarlo.

Por eso conviene correrlo por tramos (`--desde` / `--hasta`) y de noche, y por eso es
reanudable: `--saltear-cargados` arranca desde el último día que ya está en la tabla y
`--solo-faltantes` saltea todo lo ya resuelto —cargado o declarado vacío por la fuente—.

**Antes de bajar nada, mirá `--desde-precios-fob`.** La tabla `public.precios_fob` es un volcado
previo de esta misma API que ya cubre 1993-01-04 -> 2017-04-19 y se importa por SQL, sin un solo
request. Verificado contra la API: sobre las filas en común, 419 comparadas y **cero** con valor
distinto. Dos límites que hay que conocer:

  - **Tiene un agujero de 2008 a 2011** (2009 y 2010 enteros, más buena parte de 2008 y 2011, y
    ~35 días de más en 2004). NO es de la fuente: la API sí responde esas fechas. Hay que taparlo
    por API.
  - **Recorta la curva forward.** En varios tramos guarda sólo la ventana de embarque más
    cercana. Eso NO afecta el PRP —la ventana spot es justamente la más cercana y está siempre—
    pero las filas importadas no traen el resto de la curva.

El camino completo, entonces:

    python -m etl fob_granos load-history --desde-precios-fob   # instantáneo, sin requests
    python -m etl fob_granos load-history --solo-faltantes      # sólo los días que faltan

La decisión de cómo insertar se toma AÑO POR AÑO:
  - año sin ninguna fila -> `bulk_insert` (append-only sin dedup, una transacción por año);
  - año que ya tiene datos -> `insert_if_changed`, más lento pero idempotente.
Así una corrida cortada se reanuda rápido y correr `run` antes no condena el backfill entero
al camino lento.

Flags:
  --desde AAAA-MM-DD   primer día (default: 1993-01-04, el primero que responde la API)
  --hasta AAAA-MM-DD   último día (default: ayer; una fecha futura se recorta sola)
  --pausa SEG          espera entre requests (default 4.0; ver la cuenta de arriba)
  --saltear-cargados   arrancar desde el día siguiente al último cargado
  --solo-faltantes     saltear los días ya cargados y los que la fuente declaró vacíos
  --desde-precios-fob  importar por SQL desde public.precios_fob y salir (sin requests)
  --force              re-insertar aunque no cambie (sólo en el camino con dedup)
"""
from __future__ import annotations

import argparse
import datetime as dt
import itertools
import time

import urllib3

from etl.core import db, report
from . import config, source

COLS = ["producto", "date", "embarque_desde", "embarque_hasta", "valor",
        "posicion", "circular", "estado", "fuente"]
PAUSA_DEFAULT = 4.0


# Tabla con el volcado previo de la API y el mapeo posición -> producto para importarla.
TABLA_PREVIA = "precios_fob"


def _dias_resueltos(conn, desde: dt.date, hasta: dt.date) -> set:
    """Días que ya no hace falta pedir: los que tienen filas y los que la fuente declaró vacíos.

    Las dos mitades importan. Sin la primera el backfill re-baja lo ya cargado; sin la segunda
    vuelve a gastar un request en cada uno de los ~1.200 feriados entre 1993 y 2017, que nunca
    van a traer nada.

    El criterio de "tiene filas" es deliberadamente laxo: un día con las cuatro posiciones y uno
    con tres se ven igual. Volver a pedir el día parcial no arreglaría nada —la API devolvería lo
    mismo— y el costo de equivocarse para el otro lado (re-bajar ~8.600 días) es justo el
    problema que este flag evita. Para forzar la relectura de un tramo, acotarlo con
    `--desde` / `--hasta` sin este flag.
    """
    with conn.cursor() as cur:
        cur.execute(f"select date from {config.TABLE} where date between %s and %s "
                    f"union select date from {config.SIN_DATO_TABLE} where date between %s and %s",
                    (desde, hasta, desde, hasta))
        return {d for (d,) in cur.fetchall()}


def _importar_precios_fob(conn, rep, desde: dt.date, hasta: dt.date) -> int:
    """Importa por SQL desde `precios_fob` las filas de los cuatro granos. Idempotente.

    El `distinct` no es decorativo: la tabla de origen repite la misma (fecha, posición, ventana)
    ~9.000 veces con el mismo precio. El `not exists` hace la carga re-corrible sin apilar
    snapshots iguales en una tabla append-only.
    """
    mapeo = ", ".join(f"('{pos}','{prod}')" for prod, (pos, _, _) in config.PRODUCTOS.items())
    sql = f"""
        insert into {config.TABLE}
            (producto, date, embarque_desde, embarque_hasta, valor, posicion, circular,
             estado, fuente)
        select distinct
            m.producto, p.date,
            make_date(p.ano_desde, p.mes_desde, 1), make_date(p.ano_hasta, p.mes_hasta, 1),
            p.precio::double precision, p.posicion, p.circular,
            %s, %s
        from {TABLA_PREVIA} p
        join (values {mapeo}) m(posicion, producto) on m.posicion = p.posicion
        where p.date between %s and %s
          and p.precio is not null
          and not exists (
              select 1 from {config.TABLE} e
              where e.producto = m.producto
                and e.date = p.date
                and e.embarque_desde = make_date(p.ano_desde, p.mes_desde, 1)
                and e.embarque_hasta = make_date(p.ano_hasta, p.mes_hasta, 1))
    """
    fuente = f"tabla {TABLA_PREVIA} (volcado previo de la API de MAGyP)"
    with conn.cursor() as cur:
        cur.execute(sql, (config.ESTADO, fuente, desde, hasta))
        n = cur.rowcount
    conn.commit()
    return n


def _anio_cargado(conn, anio: int) -> bool:
    with conn.cursor() as cur:
        cur.execute(f"select 1 from {config.TABLE} where date >= %s and date <= %s limit 1",
                    (dt.date(anio, 1, 1), dt.date(anio, 12, 31)))
        return cur.fetchone() is not None


def main(argv=None) -> None:
    hoy = dt.date.today()
    ap = argparse.ArgumentParser(prog="etl fob_granos load-history",
                                 description="Carga histórica diaria 1993 -> hoy (one-off).")
    ap.add_argument("--desde", type=dt.date.fromisoformat, metavar="AAAA-MM-DD",
                    default=dt.date.fromisoformat(config.START),
                    help=f"primer día (default: {config.START})")
    ap.add_argument("--hasta", type=dt.date.fromisoformat, metavar="AAAA-MM-DD",
                    default=hoy, help="último día (default: hoy)")
    ap.add_argument("--pausa", type=float, default=PAUSA_DEFAULT, metavar="SEG",
                    help=f"espera entre requests (default: {PAUSA_DEFAULT})")
    ap.add_argument("--saltear-cargados", action="store_true",
                    help="arrancar desde el día siguiente al último cargado")
    ap.add_argument("--solo-faltantes", action="store_true",
                    help="saltear los días que ya tienen alguna fila cargada")
    ap.add_argument("--desde-precios-fob", action="store_true",
                    help=f"importar por SQL desde public.{TABLA_PREVIA} y salir (sin requests)")
    ap.add_argument("--force", action="store_true",
                    help="re-insertar aunque no cambie (sólo con el año ya poblado)")
    args = ap.parse_args(argv)
    urllib3.disable_warnings()

    rep = report.Report("fob_granos", "load-history")
    conn = db.get_conn()
    try:
        desde = args.desde
        if args.desde_precios_fob:
            n = _importar_precios_fob(conn, rep, desde, args.hasta)
            rep.info(f"{desde}..{args.hasta} | importadas desde {TABLA_PREVIA}: {n} filas")
            for _ in range(n):
                rep.tally("nuevo")
            rep.summary()
            return
        if args.saltear_cargados:
            ultimo = db.last_date(conn, table=config.TABLE)
            if ultimo:
                desde = max(desde, ultimo + dt.timedelta(days=1))
        # Un --hasta futuro no es un error del usuario, es lo natural al escribir el año
        # completo (`--hasta 2026-12-31`). Pero pedir días que todavía no pasaron sólo gasta
        # requests, así que se recorta acá y se avisa.
        hasta = args.hasta
        if hasta >= hoy:
            if hasta > hoy:
                rep.info(f"--hasta {args.hasta} es futuro: se recorta a {hoy - dt.timedelta(days=1)}")
            hasta = hoy - dt.timedelta(days=1)

        dias = list(source.dias_habiles(desde, hasta))
        totales = len(dias)
        if args.solo_faltantes:
            ya = _dias_resueltos(conn, desde, hasta)
            dias = [d for d in dias if d not in ya]
        horas = len(dias) * args.pausa / 3600
        rep.info(f"{desde}..{hasta} | dias habiles: {len(dias)}"
                 + (f" de {totales} (faltantes)" if args.solo_faltantes else "")
                 + f" | pausa: {args.pausa}s | estimado: {horas:.1f} h")
        if not dias:
            rep.summary()
            return

        for anio, grupo in itertools.groupby(dias, key=lambda d: d.year):
            grupo = list(grupo)
            # Con --solo-faltantes TODOS los días que llegan acá tienen cero filas en la
            # tabla: el filtro ya sacó los cargados. Mandarlos igual por el camino con dedup
            # gasta un SELECT + COMMIT por fila para confirmar algo que ya sabemos, y contra una
            # base remota eso cuadruplica el tiempo de un año. Medido el 2026-09-11: los años
            # que caían en dedup por tener un solo mes de muestra cargado iban a ~22 s por día
            # contra los ~5,3 s de los que iban por bulk.
            masivo = args.solo_faltantes or not _anio_cargado(conn, anio)
            lote: list[tuple] = []
            con_datos = 0
            for i, fecha in enumerate(grupo):
                try:
                    filas, url = source.get_dia(fecha)
                except source.DiaSinCotizacion:
                    # Queda registrado para que el próximo --solo-faltantes no lo vuelva a pedir.
                    source.marcar_sin_dato(conn, fecha)
                    rep.tally("no_publicado")
                    continue
                except Exception as e:  # noqa: BLE001
                    rep.note(f"{fecha}", f"error bajando: {e}", failure=True)
                    continue
                finally:
                    time.sleep(args.pausa)
                con_datos += 1
                for f in filas:
                    if masivo:
                        lote.append((f["producto"], f["date"], f["embarque_desde"],
                                     f["embarque_hasta"], f["valor"], f["posicion"],
                                     f["circular"], config.ESTADO, url))
                    else:
                        rep.tally(db.insert_if_changed(
                            conn, table=config.TABLE, key_cols=config.KEY_COLS,
                            key_vals=[f["producto"], f["date"], f["embarque_desde"],
                                      f["embarque_hasta"]],
                            value_cols=config.VALUE_COLS, row={"valor": f["valor"]},
                            estado=config.ESTADO, fuente=url, force=args.force,
                            extra={"posicion": f["posicion"], "circular": f["circular"]},
                        ))
            if masivo and lote:
                # Una transacción por año: acota la memoria y deja el avance commiteado, así una
                # corrida cortada a la mitad no pierde los años ya cargados.
                n = db.bulk_insert(conn, table=config.TABLE, cols=COLS, rows=lote)
                for _ in range(n):
                    rep.tally("nuevo")
            rep.info(f"{anio}: dias con datos={con_datos}/{len(grupo)}  "
                     f"modo={'bulk' if masivo else 'dedup'}"
                     + (f"  filas={len(lote)}" if masivo else ""))
    finally:
        conn.close()
    rep.summary()


if __name__ == "__main__":
    main()
