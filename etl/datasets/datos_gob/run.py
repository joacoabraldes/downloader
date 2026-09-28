"""ETL incremental de las series de organismos públicos (INDEC y otros -> Postgres).

TRES fuentes, con prioridad explícita:

  1. Cuadros del INDEC — PRIMARIA para las series que listan esas configs. Publican antes que la
     API, y en comercio exterior además traen la versión revisada que la API no tiene.
       - CSV (`source_csv.py`, `config.SERIES_CSV`): las 5 del índice de salarios.
       - XLS (`source_xls.py`, `config.SERIES_XLS`): expo_total, impo_total y saldo_total.
  2. API de series de tiempo (`source.py`, `config.SERIES_META`) — primaria para todo el resto y
     RESPALDO de las anteriores: cubre una serie del grupo 1 sólo si su cuadro no estuvo, y aun
     así NO pisa los meses que ya escribió el cuadro (ver `del_cuadro` en `main`).

La prioridad la decide quién ESCRIBE, no quién escribió último: `run.py` saca del pedido a la API
las series que los cuadros ya cubrieron. `etl_datos_gob_actual` desempata por `ingested_at desc` y
no tiene un CASE de precedencia por fuente, así que si las dos escribieran el mismo mes el ganador
dependería del orden de ejecución. La columna `fuente` deja registrado por cuál entró cada fila.

ESTADO de la serie observada:
  - 'definitivo'  el CSV, la API y los meses SIN marca de la planilla XLS.
  - 'provisorio'  los meses que la planilla XLS marca como provisorios ('*' en el año) o
                  estimados ('e' en el mes). Ver `cargar_xls`.
`insert_if_changed` absorbe las revisiones de los organismos.

No hay `load_history`: la API devuelve la serie completa, así que la primera corrida carga todo
el histórico y las siguientes sólo agregan el período nuevo.

Las series se guardan tal como las publica el organismo. Sobre eso la corrida agrega la serie
desestacionalizada, que llega por DOS rutas excluyentes y ambas terminan en la misma fila
(estado='desestacionalizado'), distinguibles por `fuente` y `parametros`:

  - X-13 propio, sobre la serie REAL y no la nominal — las 2 de ventas y expo/impo (el saldo
    NO: puede ser negativo). Se configura en el bloque [datos_gob] de etl/series_desest.toml.
  - la ajustada que publica el propio organismo — `DESEST_OFICIAL` en config.py. Si INDEC ya la
    calcula, no le corremos X-13 encima.

Sumar una serie = agregar una fila a `SERIES_META` en config.py. No hay que tocar este archivo.

Flags:
  --force          insertar snapshot aunque no haya cambiado
  --serie NOMBRE   correr sólo esa serie (repetible)
  --desde YYYY-MM  ignorar los meses anteriores
"""
from __future__ import annotations

import argparse
import datetime as dt

import requests
from psycopg2.extras import Json

from etl.core import db, desest_params, report, seasonal
from . import config, source, source_csv, source_xls


def _mes(texto: str) -> dt.date:
    y, m = map(int, texto.split("-"))
    return dt.date(y, m, 1)


def sincronizar_dimension(conn) -> None:
    """Upsert de `etl_datos_gob_series` desde config.SERIES_META (única fuente de verdad).

    `dias_max_dato` sale de config.DIAS_MAX_DATO: es el umbral de frescura POR SERIE que lee la
    vista `etl_datos_gob_salud`.

    `deflactable` NO se declara en config: se deriva de `deflactor`. Es la columna que expone
    `etl_datos_gob_actual` desde antes de que hubiera dos deflactores, y de esa vista cuelgan las
    unificadas de schema_unified.sql; se la sigue escribiendo para no romper ese contrato, pero
    la verdad es el nombre del índice.
    """
    desconocidos = {meta[4] for meta in config.SERIES_META.values()
                    if meta[4] is not None} - config.DEFLACTORES
    if desconocidos:
        # Un typo acá no rompe nada visible: el JOIN de la vista _real sale vacío y la serie
        # aparece sin valor real, indistinguible de una que no se deflacta a propósito.
        raise ValueError(f"deflactor(es) desconocido(s) en SERIES_META: {sorted(desconocidos)}. "
                         f"Opciones: {sorted(config.DEFLACTORES)}")
    sin_umbral = set(config.SERIES_META) - set(config.DIAS_MAX_DATO)
    sobrantes = set(config.DIAS_MAX_DATO) - set(config.SERIES_META)
    if sin_umbral or sobrantes:
        # Una serie sin umbral no rompe nada visible: `etl_datos_gob_salud` la mostraría en `ok`
        # para siempre, que es justo la ceguera que ese umbral existe para evitar.
        raise ValueError(f"DIAS_MAX_DATO no cuadra con SERIES_META. Sin umbral: "
                         f"{sorted(sin_umbral)}; sobrantes: {sorted(sobrantes)}")
    filas = [(serie, meta[0], meta[1], meta[2], meta[3], meta[4], meta[4] is not None, i,
              config.REAL_DESDE.get(serie), config.DIAS_MAX_DATO[serie])
             for i, (serie, meta) in enumerate(config.SERIES_META.items(), start=1)]
    with conn.cursor() as cur:
        cur.executemany(
            """insert into etl_datos_gob_series
                 (serie, id_api, nombre, unidad, organismo, deflactor, deflactable, orden,
                  real_desde, dias_max_dato)
               values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
               on conflict (serie) do update set
                 id_api = excluded.id_api, nombre = excluded.nombre, unidad = excluded.unidad,
                 organismo = excluded.organismo, deflactor = excluded.deflactor,
                 deflactable = excluded.deflactable,
                 orden = excluded.orden, real_desde = excluded.real_desde,
                 dias_max_dato = excluded.dias_max_dato""",
            filas)
    conn.commit()


def cargar_csv(conn, rep, series, *, desde=None, force=False) -> set[str]:
    """Carga los cuadros CSV del INDEC (`config.SERIES_CSV`), fuente PRIMARIA de esas series.

    Devuelve el conjunto de series que quedaron cubiertas. Las que NO estén ahí las tiene que
    cubrir la API, que para estas series es el respaldo.

    Un cuadro que se cae NO aborta la corrida: se avisa y esas series vuelven al carril de la
    API. Perder dos meses de adelanto es mucho mejor que quedarse sin serie, y el dato de la API
    es el mismo número (ver la medición en config.SERIES_CSV).
    """
    cubiertas: set[str] = set()
    for nombre, cuadro in config.SERIES_CSV.items():
        pedidas = {s for s in cuadro["columnas"].values() if s in series}
        if not pedidas:
            continue
        try:
            por_serie, descartes = source_csv.get_cuadro(cuadro["url"], cuadro["columnas"])
        except (requests.RequestException, ValueError) as e:
            # FALLA de la corrida, no un aviso suelto. Antes esto era `rep.info`, que sólo
            # imprime: la caída no llegaba a `etl_control_ejecucion.fallas` ni al exit code, y el
            # cron salía verde para siempre mientras las series volvían en silencio a la API
            # atrasada —justo lo que este cambio existe para evitar—.
            #
            # Que la corrida quede en 'falla' NO significa que no entró nada: la API cubre estas
            # series unas líneas más abajo y el dato sigue siendo válido, sólo más viejo. Es el
            # mismo criterio que el resto del repo, donde una fuente caída marca falla aunque el
            # dataset cargue parcialmente.
            #
            # El except es ACOTADO a propósito: `requests.RequestException` (red/HTTP) y el
            # `ValueError` que levanta `get_cuadro` ante formato inesperado. Un TypeError o un
            # KeyError son bugs NUESTROS y tienen que tumbar la corrida con su traceback, no
            # disfrazarse de "el INDEC estaba caído".
            rep.error(f"cuadro '{nombre}' no disponible ({e}); "
                      f"caen a la API de respaldo: {', '.join(sorted(pedidas))}")
            continue
        for d in descartes:
            rep.info(f"csv/{nombre}: descartado -> {d}")
        for serie in sorted(pedidas):
            filas = por_serie.get(serie) or []
            if desde:
                filas = [(f, v) for f, v in filas if f >= desde]
            if not filas:
                rep.note(serie, f"cuadro '{nombre}' sin datos", status="no_publicado")
                continue
            for fecha, valor in filas:
                rep.tally(db.insert_if_changed(
                    conn, table=config.TABLE, key_cols=config.KEY_COLS,
                    key_vals=[serie, fecha], value_cols=config.VALUE_COLS,
                    row={"valor": valor}, estado="definitivo",
                    fuente=cuadro["url"], force=force,
                ))
            cubiertas.add(serie)
            rep.info(f"{serie:24} {len(filas):>4} meses  "
                     f"{filas[0][0]:%Y-%m}..{filas[-1][0]:%Y-%m}  ult={filas[-1][1]:g}  "
                     f"[csv INDEC]")
    return cubiertas


def _actual_por_mes(conn, series) -> dict[tuple[str, dt.date], tuple[str | None, str | None]]:
    """(serie, mes) -> (estado, fuente) de la fila que HOY muestra `etl_datos_gob_actual`."""
    with conn.cursor() as cur:
        cur.execute(f"select serie, date, estado, fuente from {config.ACTUAL_VIEW} "
                    f"where serie = any(%s)", (list(series),))
        return {(s, d): (e, f) for s, d, e, f in cur.fetchall()}


def cargar_xls(conn, rep, series, *, desde=None, force=False) -> set[str]:
    """Carga las planillas .xls del INDEC (`config.SERIES_XLS`), fuente PRIMARIA de esas series.

    Mismo contrato que `cargar_csv`: devuelve las series cubiertas, y una planilla caída es
    `rep.error` (falla de la corrida) y esas series caen a la API de respaldo.

    ESTADO. La planilla marca sus meses: '*' en la celda del año (todo el año provisorio; hoy
    2024, 2025 y 2026) y 'e' pegada al mes (estimado; hoy agosto-2026). Un mes con cualquiera de
    las dos marcas entra 'provisorio'; sin marca, 'definitivo'. Se juntan las dos marcas en un solo
    estado porque para el consumidor significan lo mismo —el INDEC lo va a revisar— y porque el
    repo no tiene un estado 'estimado' en ningún otro dataset. La 'e' de la leyenda dice "para
    exportaciones", pero la marca está en la FILA y el saldo depende de la expo; además ese mes cae
    en un año con '*', así que las tres series quedan provisorias igual.

    POR QUÉ `etl_datos_gob_actual` SIGUE SIN CASE DE PRECEDENCIA (definitivo > provisorio, como en
    cemento): acá el 'definitivo' de la API es VIEJO. La API no marca nada y trae 'definitivo'
    para meses que el INDEC todavía llama provisorios, con una versión anterior del número
    (julio-2026 de impo: 6.738,68 en la API, 6.755,73 revisado en la planilla). Un CASE haría ganar
    ese número viejo sobre la revisión más nueva. Se queda `ingested_at desc`: gana lo último
    publicado por la fuente primaria, y el estado es un atributo de esa fila, no un rango.

    Lo que eso exige, y resuelve el `forzar` de abajo: cuando el INDEC le saca la marca a un mes
    SIN cambiar el número, `insert_if_changed` compara contra el último snapshot de (clave,
    'definitivo') —el de la API, con el mismo valor— y devuelve 'sin_cambios'. La fila vigente
    seguiría siendo la 'provisorio' para siempre. Por eso, si el estado que trae la planilla es
    distinto del que muestra hoy la vista, se inserta aunque el valor no haya cambiado. El caso
    inverso —un mes 'definitivo' de la API que la planilla marca provisorio— no lo necesita (no hay
    snapshot 'provisorio' previo, entra 'nuevo'), pero sí lo necesita si ya hubo uno.
    """
    cubiertas: set[str] = set()
    for nombre, cuadro in config.SERIES_XLS.items():
        pedidas = {s for s in cuadro["columnas"].values() if s in series}
        if not pedidas:
            continue
        try:
            por_serie, descartes = source_xls.get_cuadro(
                cuadro["url"], cuadro["columnas"], cuadro["encabezados"], cuadro.get("unidad"))
            vigente = _actual_por_mes(conn, pedidas)
            # Planilla vieja servida por una caché: pasa todas las validaciones de formato pero
            # termina antes de lo que ella misma ya cargó. Se rechaza entera, porque sus valores
            # podrían ser una versión anterior de meses ya revisados.
            ya_cargado = max((f for (_, f), (_, fu) in vigente.items() if fu == cuadro["url"]),
                             default=None)
            trae = max((f for filas in por_serie.values() for f, _, _ in filas), default=None)
            if ya_cargado and (trae is None or trae < ya_cargado):
                raise ValueError(f"trae hasta {trae}, pero la base ya tiene hasta {ya_cargado} "
                                 f"de esta misma planilla (¿versión vieja en caché?)")
        except (requests.RequestException, ValueError) as e:
            # Mismo criterio y mismo except acotado que `cargar_csv`: falla de la corrida, y las
            # series caen a la API. Un bug nuestro (TypeError, KeyError) tiene que tumbar la
            # corrida con su traceback.
            rep.error(f"planilla '{nombre}' no disponible ({e}); "
                      f"caen a la API de respaldo: {', '.join(sorted(pedidas))}")
            continue
        for d in descartes:
            rep.info(f"xls/{nombre}: descartado -> {d}")
        for serie in sorted(pedidas):
            filas = por_serie.get(serie) or []
            if desde:
                filas = [(f, v, e) for f, v, e in filas if f >= desde]
            if not filas:
                rep.note(serie, f"planilla '{nombre}' sin datos", status="no_publicado")
                continue
            for fecha, valor, estado in filas:
                previo = vigente.get((serie, fecha))
                forzar = force or (previo is not None and previo[0] != estado)
                rep.tally(db.insert_if_changed(
                    conn, table=config.TABLE, key_cols=config.KEY_COLS,
                    key_vals=[serie, fecha], value_cols=config.VALUE_COLS,
                    row={"valor": valor}, estado=estado,
                    fuente=cuadro["url"], force=forzar,
                ))
            cubiertas.add(serie)
            provisorios = sum(1 for *_, e in filas if e == "provisorio")
            rep.info(f"{serie:24} {len(filas):>4} meses  "
                     f"{filas[0][0]:%Y-%m}..{filas[-1][0]:%Y-%m}  ult={filas[-1][1]:g} "
                     f"({filas[-1][2]})  provisorios={provisorios}  [xls INDEC]")
    return cubiertas


def cargar_desest_oficial(conn, series, *, desde=None) -> list[dict]:
    """Baja las desestacionalizadas que publica el propio organismo (config.DESEST_OFICIAL).

    Entran por el mismo carril que el X-13 propio —estado='desestacionalizado' bajo el slug de la
    serie base— para que caigan en `valor_desest` de la vista de consumo. Lo que las distingue es
    `fuente` (URL de la API, no 'census x13') y `parametros` ({"origen": "indec"}).

    UPSERT y no `insert_if_changed`: sobre estado='desestacionalizado' hay un unique index parcial
    por (serie, date), así que este carril NO es append-only. Un snapshot nuevo lo violaría.

    Devuelve resultados con la forma de `seasonal._result` para que los reporte el bloque
    `[dataset / desest]`, que es donde se cuentan los upserts de ESE carril. Reportarlos con
    `rep.info` dejaba las filas escritas sin aparecer en ningún contador de la corrida.
    """
    resultados: list[dict] = []
    for serie, serie_id in config.DESEST_OFICIAL.items():
        if serie not in series:
            continue
        try:
            filas, descartes = source.get_serie(serie_id)
        except Exception as e:
            resultados.append(seasonal._result(serie, "error",
                                               reason=f"bajando desest oficial {serie_id}: {e}"))
            continue
        for d in descartes:
            print(f"  {serie} (desest oficial): descartado -> {d}")
        if desde:
            filas = [(f, v) for f, v in filas if f >= desde]
        if not filas:
            resultados.append(seasonal._result(serie, "skipped",
                                               reason="desest oficial sin datos"))
            continue
        params = Json({"origen": "indec", "id_api": serie_id,
                       "nota": "serie desestacionalizada publicada por el organismo; "
                               "no es una corrida X-13 de este repo"})
        fuente = source.url_serie(serie_id)
        with conn.cursor() as cur:
            for fecha, valor in filas:
                cur.execute(
                    f"""insert into {config.TABLE} (serie, date, valor, estado, fuente, parametros)
                        values (%s, %s, %s, 'desestacionalizado', %s, %s)
                        on conflict (serie, date) where estado = 'desestacionalizado'
                        do update set valor = excluded.valor, fuente = excluded.fuente,
                                      parametros = excluded.parametros, ingested_at = now()""",
                    (serie, fecha, valor, fuente, params))
        conn.commit()
        resultados.append(seasonal._result(serie, "ok", n=len(filas), mode="indec"))
    return resultados


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

    # INVARIANTE: ninguna serie puede tener a la vez X-13 propio y desest oficial. Las dos
    # escriben (serie, date, estado='desestacionalizado') y el unique index parcial deja UNA sola
    # fila: la última en correr pisaría a la otra sin ruido, y `valor_desest` mezclaría orígenes
    # según el orden de ejecución. Se valida acá, antes de tocar la base y la red.
    solapadas = sorted({s for s, _ in desest_params.jobs_for("datos_gob")}
                       & set(config.DESEST_OFICIAL))
    if solapadas:
        ap.error(f"serie(s) con desest duplicada: {', '.join(solapadas)}. Están en DESEST_OFICIAL "
                 f"(config.py) y en el bloque [datos_gob] de etl/series_desest.toml. Va una sola: "
                 f"si el organismo publica la ajustada, sacarla del toml.")

    rep = report.Report("datos_gob", "run")
    rep.info(f"fuente: {source.BASE} | series: {len(series)}")
    conn = db.get_conn()
    try:
        sincronizar_dimension(conn)

        # PRIMERO los cuadros del INDEC (CSV y XLS): son la fuente primaria de las series que
        # listan config.SERIES_CSV y config.SERIES_XLS. Lo que cubran NO se vuelve a pedir a la
        # API, y así la prioridad queda decidida por quién ESCRIBE y no por quién escribió último. `etl_datos_gob_actual` desempata por `ingested_at desc`
        # y no tiene un CASE de precedencia por fuente: si las dos escribieran el mismo mes, el
        # ganador dependería del orden de ejecución, que es exactamente lo que no queremos.
        cubiertas = cargar_csv(conn, rep, series, desde=args.desde, force=args.force)
        cubiertas |= cargar_xls(conn, rep, series, desde=args.desde, force=args.force)
        respaldo = [s for s in series if s not in cubiertas and s in config.CUADRO_POR_SERIE]
        # Meses que la fila vigente ya trae del cuadro primario: el respaldo NO los toca. Sin
        # esto, con la planilla caída la API volvería a escribir su versión VIEJA de 2022-2023
        # (difiere hasta 149 USD M por mes, ver SERIES_XLS): `insert_if_changed` la compara contra
        # el último 'definitivo', que es el del cuadro, ve un cambio y la inserta como la más
        # nueva. El respaldo existe para traer meses que el cuadro no tiene, no para revertirlo.
        del_cuadro = {k for k, (_, fuente) in _actual_por_mes(conn, respaldo).items()
                      if fuente == config.CUADRO_POR_SERIE[k[0]]} if respaldo else set()
        for serie in series:
            if serie in cubiertas:
                continue
            if serie in config.CUADRO_POR_SERIE:
                # "no la cubrió", no "no existe el cuadro": se llega acá por tres caminos —el
                # cuadro se cayó, la columna vino vacía, o `--desde` filtró todas sus filas—.
                rep.info(f"{serie}: el cuadro del INDEC no la cubrió, se usa la API de respaldo")
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
            if serie in config.CUADRO_POR_SERIE and filas:
                antes = len(filas)
                filas = [(f, v) for f, v in filas if (serie, f) not in del_cuadro]
                if antes != len(filas):
                    rep.info(f"{serie}: respaldo API sin tocar {antes - len(filas)} meses que ya "
                             f"trae el cuadro del INDEC")
                if not filas:
                    rep.note(serie, "la API no trae ningún mes que no tenga ya el cuadro",
                             status="sin_cambios")
                    continue
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
        # Se BAJA acá (es un GET a la API) pero se REPORTA en el bloque de desest, porque escribe
        # en ese carril y sus upserts se cuentan ahí. `--force` no viaja: este camino siempre
        # upsertea, no tiene el fast-path de "si no cambió no escribas" que `--force` saltea.
        desest_oficial = ([] if args.no_desest
                          else cargar_desest_oficial(conn, series, desde=args.desde))
        rep.summary()

        # X-13 sobre la serie REAL (ver la clave `view` en etl/series_desest.toml). A diferencia
        # del resto del repo NO se condiciona a `rep.changed`: el insumo es la serie deflactada,
        # así que un mes nuevo del IPC cambia toda la serie real aunque las ventas no se hayan
        # movido, y la desest quedaría vieja.
        if not args.no_desest:
            seasonal.run_desest(conn, "datos_gob",
                                desest_params.build_jobs("datos_gob", keep_dir=args.x13_out),
                                extra=desest_oficial)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
