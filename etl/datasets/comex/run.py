"""ETL incremental de los índices de comercio exterior del INDEC (ICA).

Baja las dos planillas (URLs fijas), parsea las 18 series y snapshotea cada mes con el estado
que declara INDEC: 'provisorio' mientras el año está marcado con asterisco, 'definitivo'
cuando lo cierra. Al final, sólo si hubo datos nuevos o actualizados, desestacionaliza (X-13)
las 6 series de cantidad.

Las planillas traen SIEMPRE el histórico completo (2004-01 →), así que no hay `load-history`:
la primera corrida carga todo sola.

Qué se re-procesa en cada corrida: los meses de los años que INDEC todavía marca provisorios
(hoy 2024-2026, ~30 meses) y cualquier `(serie, mes, estado)` que no esté cargado —eso cubre
los meses nuevos y el pasaje provisorio → definitivo cuando INDEC cierra un año—. Los meses ya
cargados como definitivos se saltean porque no cambian; `--full` los revisa igual (para un
rebase o una revisión retroactiva). La primera corrida, con la tabla vacía, entra por el camino
de backfill masivo: no hay nada contra qué deduplicar y son ~4900 filas.

Flags:
  --full         re-procesar todos los meses, incluidos los ya definitivos
  --force        insertar snapshot aunque no haya cambiado
  --no-desest    saltear la desestacionalización X-13
  --x13-out DIR  guardar la salida completa de X-13 en DIR
"""
from __future__ import annotations

import argparse

from etl.core import db, desest_params, report, seasonal
from . import config, source


def _cargados(conn) -> set:
    """{(serie, date, estado)} ya presentes en la tabla (sin la desest).

    Una sola query de ~4900 filas que reemplaza al `last_date` como criterio del incremental.
    Hace falta el ESTADO, no sólo la fecha: cuando INDEC cierra un año provisorio publica los
    mismos meses sin el asterisco, y ésa es la única señal de que el número quedó firme. Con un
    criterio por fecha (`date > ultimo cargado`) esos meses caerían fuera de la ventana y el
    snapshot 'definitivo' no entraría nunca — la serie se quedaría marcada provisoria para
    siempre.
    """
    with conn.cursor() as cur:
        cur.execute(f"select distinct serie, date, estado from {config.TABLE} "
                    f"where estado is distinct from 'desestacionalizado'")
        return {(s, d, e) for s, d, e in cur.fetchall()}


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="etl comex",
                                 description="ETL índices de comercio exterior (INDEC / ICA)")
    ap.add_argument("--full", action="store_true",
                    help="re-procesar todos los meses (no sólo los provisorios y los nuevos)")
    ap.add_argument("--force", action="store_true", help="insertar aunque no cambie")
    ap.add_argument("--no-desest", action="store_true",
                    help="saltear la desestacionalización X-13")
    ap.add_argument("--x13-out", metavar="DIR",
                    help="guardar la salida de X-13 (html/factores/diagnósticos) en DIR")
    args = ap.parse_args(argv)

    rep = report.Report("comex", "run")
    conn = db.get_conn()
    try:
        try:
            data, estados, avisos, fuente = source.get_latest()
        except Exception as e:
            rep.error(f"bajando/parseando: {e}")
            rep.summary()
            return
        if not data or not estados:
            rep.error("las planillas no trajeron ninguna serie o ningún mes")
            rep.summary()
            return
        # Bloque de rubro desconocido o planillas que dejaron de coincidir: se reporta como
        # falla (dispara el mail del cron) pero se ingesta lo que sí se pudo leer.
        for aviso in avisos:
            rep.error(aviso)

        fechas = sorted(estados)
        faltantes = [s for s in config.SERIES if s not in data]
        if faltantes:
            rep.error(f"series ausentes en las planillas: {', '.join(faltantes)}")

        cargados = _cargados(conn)
        if not cargados:
            # Primera carga: backfill masivo en una transacción. Sin nada contra qué deduplicar,
            # el SELECT+COMMIT por fila de insert_if_changed no aporta y cuesta ~4900 roundtrips.
            rep.info(f"fuente: {fuente} | series: {len(data)} | "
                     f"meses: {fechas[0]:%Y-%m}..{fechas[-1]:%Y-%m} | modo: backfill masivo")
            rows = [
                (serie, fecha, valor, estados[fecha], fuente)
                for serie in config.SERIES
                for fecha, valor in sorted(data.get(serie, {}).items())
            ]
            n = db.bulk_insert(conn, table=config.TABLE,
                               cols=["serie", "date", "valor", "estado", "fuente"], rows=rows)
            for _ in range(n):
                rep.tally("nuevo")
        else:
            # Incremental. Se procesa un (serie, mes) si:
            #   - el mes es provisorio: INDEC todavía puede revisarlo, hay que releerlo siempre;
            #   - o ese (serie, mes, estado) no está cargado: cubre los meses nuevos Y el
            #     pasaje provisorio -> definitivo cuando INDEC cierra un año.
            # Los meses ya cargados como definitivos se saltean: no cambian. `--full` los revisa
            # igual (rebase, revisión retroactiva).
            pendientes = [
                (serie, fecha)
                for serie in config.SERIES
                for fecha in fechas
                if args.full or estados[fecha] == "provisorio"
                or (serie, fecha, estados[fecha]) not in cargados
            ]
            modo = ("full" if args.full
                    else f"provisorios + faltantes ({len(pendientes)} filas)")
            rep.info(f"fuente: {fuente} | series: {len(data)} | "
                     f"meses: {fechas[0]:%Y-%m}..{fechas[-1]:%Y-%m} | modo: {modo}")
            for serie, fecha in pendientes:
                valor = data.get(serie, {}).get(fecha)
                if valor is None:
                    continue
                status = db.insert_if_changed(
                    conn, table=config.TABLE, key_cols=config.KEY_COLS,
                    key_vals=[serie, fecha], value_cols=config.VALUE_COLS,
                    row={"valor": float(valor)},
                    estado=estados[fecha], fuente=fuente, force=args.force,
                )
                rep.tally(status)
        rep.summary()

        if args.no_desest:
            pass
        elif not rep.changed:
            print("sin datos nuevos: no se desestacionaliza")
        else:
            seasonal.run_desest(conn, "comex",
                                desest_params.build_jobs("comex", keep_dir=args.x13_out))
    finally:
        conn.close()


if __name__ == "__main__":
    main()
