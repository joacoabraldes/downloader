"""ETL incremental de producción de carne bovina (MAGyP, base SENASA).

Descubre el xls de faena/producción siguiendo la cadena página → Tablero PDF → hipervínculo
embebido → xls (ver source.py), parsea la producción mensual (miles tn res con hueso) y
guarda con estado='definitivo' los meses que todavía no están. Al final, sólo si hubo datos
nuevos, desestacionaliza (X-13).

Guarda contra revisiones (sep-2026 →). El xls re-publica la serie 2019→ en cada corrida, pero
desde que MAGyP cambió de planilla (sep-2026) ya no se le cree a ciegas: en ese archivo las 12
filas de 2024 son una copia de las de 2025. Por eso, mes por mes:
  - mes SIN snapshot 'definitivo' en la base → se inserta (el incremental normal);
  - mes con 'definitivo' y diferencia relativa <= TOL_REVISION → no se toca (`sin_cambios`),
    aunque difiera en decimales: las revisiones chicas NO se absorben;
  - mes con 'definitivo' y diferencia > TOL_REVISION → NO se inserta. Se imprime una línea por
    mes con los dos valores y cuenta como `saltados` en el resumen (y en
    etl_control_ejecucion). No es falla: el dato de la base sigue siendo el bueno, y cambiarlo
    es una decisión manual (`--force` NO la saltea).
Además, `source` descarta los años cuya suma de meses no coincide con su fila de cierre
('Total 2024') y el run los informa como AVISO. 2024 cae en los dos filtros.

El histórico profundo (1998→) se carga aparte del Excel de referencia:
`python -m etl bovinos load-history`.

Flags:
  --force        re-snapshotear los meses que pasan la guarda aunque no hayan cambiado
  --no-desest    saltear la desestacionalización X-13
  --x13-out DIR  guardar la salida completa de X-13 en DIR
"""
from __future__ import annotations

import argparse

import urllib3

from etl.core import db, desest_params, report, seasonal
from . import config, source

# Diferencia relativa máxima contra el 'definitivo' de la base para considerar que el archivo
# confirma el mes. Las revisiones observadas de 2026 entre planillas van de 0,0001% a 0,09%; la
# copia errónea de 2024 difiere entre 0,5% y 12,5%.
TOL_REVISION = 0.001


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="etl bovinos",
                                 description="ETL producción de carne bovina (MAGyP)")
    ap.add_argument("--force", action="store_true", help="insertar aunque no cambie")
    ap.add_argument("--no-desest", action="store_true",
                    help="saltear la desestacionalización X-13")
    ap.add_argument("--x13-out", metavar="DIR",
                    help="guardar la salida de X-13 (html/factores/diagnósticos) en DIR")
    args = ap.parse_args(argv)
    urllib3.disable_warnings()  # cert de magyp.gob.ar (verify=False)

    rep = report.Report("bovinos", "run")
    conn = db.get_conn()
    try:
        try:
            res = source.get_latest()
        except Exception as e:
            rep.error(f"bajando/parseando: {e}")
            rep.summary()
            return
        if not res or not res[0]:
            rep.error("no se pudo descubrir/parsear el xls de faena bovina")
            rep.summary()
            return
        data, url, avisos = res
        rep.info(f"fuente: {url} | meses: {min(data):%Y-%m}..{max(data):%Y-%m}")
        for a in avisos:
            rep.info(f"AVISO {a}")
        comparados = 0
        for fecha in sorted(data):
            valor = float(data[fecha])
            key_vals = [config.MAIN_SERIE, fecha]
            prev = db.latest_values(conn, table=config.TABLE, key_cols=config.KEY_COLS,
                                    key_vals=key_vals, value_cols=config.VALUE_COLS,
                                    estado="definitivo")
            if prev is not None and prev["valor"]:
                comparados += 1
                dif = (valor - prev["valor"]) / prev["valor"]
                if abs(dif) > TOL_REVISION:
                    rep.note(fecha, f"NO se pisa: archivo={valor:.3f} base={prev['valor']:.3f} "
                                    f"({dif:+.2%}, tolerancia {TOL_REVISION:.1%})",
                             status="saltado")
                    continue
                if not args.force:
                    rep.tally("sin_cambios")
                    continue
            status = db.insert_if_changed(
                conn, table=config.TABLE, key_cols=config.KEY_COLS, key_vals=key_vals,
                value_cols=config.VALUE_COLS, row={"valor": valor},
                estado="definitivo", fuente=url, force=args.force,
            )
            if status == "nuevo":
                rep.item(fecha, status, valor=round(valor, 3))
            else:
                rep.tally(status)  # ~90 meses: se cuentan, no se imprime línea por mes
        rep.info(f"guarda: {comparados} meses comparados contra el 'definitivo' de la base "
                 f"(tolerancia {TOL_REVISION:.1%})")
        rep.summary()

        if args.no_desest:
            pass
        elif not rep.changed:
            print("sin datos nuevos: no se desestacionaliza")
        else:
            seasonal.run_desest(conn, "bovinos",
                                desest_params.build_jobs("bovinos", keep_dir=args.x13_out))
    finally:
        conn.close()


if __name__ == "__main__":
    main()
