"""Calibra X-13 para transferencias de autos contra el d11 de transfer_autos.xlsx.

Barre mode x td x seasonalma x easter, corre x13as sobre la serie observada y compara el d11
resultante contra la columna desestacionalizada de la planilla de referencia. Sirve para
(re)confirmar los parámetros del cuadro (etl/series_desest.toml, bloque [transferencias]).

## La variante ganadora reproduce EXACTO: add + td1coef + s3x5 + easter[1]

Error medio 0,0000% y máximo 0,0002% sobre los 380 meses. Y el regresor de PASCUA es toda la
diferencia: sin él, la mejor de las 27 combinaciones restantes se queda en 0,98% medio con 9,26%
de máximo. El residuo lo cantaba solo — los peores meses sin Pascua son 2002-03, 2002-04,
1996-04, 1995-04, 2005-03, 1995-03, 2020-03, 2014-03: **todos marzo o abril**. Semana Santa se
mueve entre los dos meses, así que su efecto no es estacional y el filtro no lo puede sacar.

El ancho importa tanto como el regresor: `easter[8]` deja 0,79% de error medio y `easter[15]`
0,97%, casi lo mismo que no poner nada.

## OJO: la planilla de referencia tiene CUATRO meses mal

Comparando los 380 meses de la planilla (1995-01..2026-08) contra el cuadro de la DNRPA — que
es de donde el ETL toma el dato — hay cinco diferencias, y cuatro de ellas son un error de la
planilla:

    2000-01    7.603  vs  107.603      (-100.000)
    2008-01   33.191  vs  133.191      (-100.000)
    2014-01   63.033  vs  163.033      (-100.000)
    2021-01   32.640  vs  132.640      (-100.000)
    2026-08  155.246  vs  155.717      (revisión legítima de la fuente, +0,3%)

Las cuatro primeras son exactamente -100.000: se perdió el dígito de las centenas de mil de un
número de 6 cifras. Y las cuatro caen en ENERO, que es el pico estacional de la serie. Que sea
la planilla y no el sitio lo confirman tres cosas: el cuadro de la DNRPA cierra su propio total
anual con esos valores, los meses vecinos hacen absurda la caída (dic-1999 79.954 -> ene-2000
7.603 -> feb-2000 64.306), y los 375 meses restantes coinciden dígito a dígito.

Por eso la calibración corre sobre la serie observada **de la planilla** (errores incluidos):
el objetivo es identificar QUÉ receta de X-13 produjo ese d11, y para eso hay que darle a X-13
el mismo insumo que tuvo el autor. El ETL después aplica esa receta sobre la serie CORRECTA que
baja de la DNRPA, y por eso su d11 no tiene por qué coincidir con el de la planilla alrededor de
esos cuatro eneros.

`--fuente sitio` corre el mismo barrido con la serie corregida, para ver cuánto mueve el error.

Uso: python scripts/calibrar_transferencias.py [--fuente planilla|sitio] [--top N]

El barrido completo son 108 corridas de x13as sobre 380 meses: tarda del orden de la hora.
"""
import argparse
import os
import subprocess
import sys
import tempfile

import openpyxl

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from etl.core import seasonal as S  # noqa: E402
from etl.datasets.transferencias import config as C, source  # noqa: E402

XLSX = os.path.join(os.path.dirname(__file__), os.pardir,
                    "etl", "datasets", "transferencias", "data", "transfer_autos.xlsx")

# Hoja2 = fecha | observado | desestacionalizado | (dos columnas de variaciones).
HOJA = "Hoja2"

# Anchos del regresor de Pascua a probar (0 = sin regresor). El barrido completo son
# 3 modos x 3 td x 3 filtros x len(EASTER) corridas, así que sumar anchos cuesta tiempo.
EASTER = (0, 1, 8, 15)


def load_planilla():
    """(fechas, observado, {(anio, mes): d11}) de la planilla de referencia."""
    ws = openpyxl.load_workbook(XLSX, data_only=True)[HOJA]
    dates, obs, ref = [], [], {}
    for d, v, de, *_ in ws.iter_rows(values_only=True):
        if d is None or v is None:
            continue
        dd = d.date()
        dates.append(dd)
        obs.append(float(v))
        if de is not None:
            ref[(dd.year, dd.month)] = float(de)
    return dates, obs, ref


def load_sitio(dates):
    """Serie observada bajada de la DNRPA, alineada a `dates`."""
    data = {}
    for anio in sorted({d.year for d in dates}):
        data.update(source.get_anio(anio, C.SERIES_TIPO["autos"]))
    return [data[d] for d in dates]


def run(x13, dates, obs, ref, mode, td, sma, easter):
    wd = tempfile.mkdtemp(prefix="cal_transf_")
    S._write_spc(os.path.join(wd, "serie.spc"), dates, obs, mode=mode, td=td, seasonalma=sma,
                 easter=easter)
    subprocess.run([x13, "serie"], cwd=wd, capture_output=True, text=True, timeout=200)
    p = os.path.join(wd, "serie.d11")
    if not os.path.isfile(p):
        return None
    d11 = {(d.year, d.month): v for d, v in S._parse_d11(p)}
    common = [k for k in ref if k in d11]
    if not common:
        return None
    pct = [abs(d11[k] - ref[k]) / abs(ref[k]) * 100 for k in common]
    diffs = [abs(d11[k] - ref[k]) for k in common]
    return dict(mode=mode, td=td, sma=sma, easter=easter, arima=S._arima_model(wd, "serie"),
                meanpct=sum(pct) / len(pct), meanabs=sum(diffs) / len(diffs),
                maxabs=max(diffs), maxpct=max(pct))


def main():
    ap = argparse.ArgumentParser(description="Calibra X-13 para transferencias de autos.")
    ap.add_argument("--fuente", choices=("planilla", "sitio"), default="planilla",
                    help="serie observada: la de la planilla (default) o la de la DNRPA")
    ap.add_argument("--top", type=int, default=8, help="cuántas variantes mostrar")
    args = ap.parse_args()

    x13 = S._x13_binary()
    if not x13:
        sys.exit("x13as no encontrado (setear X13PATH)")
    dates, obs, ref = load_planilla()
    if args.fuente == "sitio":
        obs = load_sitio(dates)
    print(f"serie: {len(obs)} meses {dates[0]:%Y-%m}..{dates[-1]:%Y-%m} | "
          f"observado: {args.fuente} | ref: {len(ref)}")

    results = []
    for mode in ("add", "mult", "auto"):
        for td in ("none", "td1coef", "td"):
            for sma in ("s3x5", "s3x3", "s3x9"):
                for easter in EASTER:
                    try:
                        r = run(x13, dates, obs, ref, mode, td, sma, easter)
                    except Exception:
                        r = None
                    if r:
                        results.append(r)
    results.sort(key=lambda r: r["meanpct"])
    print(f"\n{'mode':5} {'td':8} {'sma':5} {'easter':>6} {'arima':16} "
          f"{'meanpct%':>9} {'maxpct%':>9} {'meanabs':>10} {'maxabs':>10}")
    for r in results[:args.top]:
        print(f"{r['mode']:5} {r['td']:8} {r['sma']:5} {r['easter']:6} {str(r['arima']):16} "
              f"{r['meanpct']:9.4f} {r['maxpct']:9.4f} {r['meanabs']:10.2f} {r['maxabs']:10.2f}")


if __name__ == "__main__":
    main()
