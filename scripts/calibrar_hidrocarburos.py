"""Calibra X-13 para petróleo y gas contra la columna desest de los Excel de referencia.

Serie observada = la MISMA que va a la base (ver `etl/datasets/hidrocarburos/schema.sql`):
columna B del Excel hasta 2008-12 y Superset (Sec. Energía, en vivo) de 2009-01 en adelante.
Barre mode x td x seasonalma, corre x13as y compara el d11 contra la referencia (columna C).

## Por qué la ventana de calibración corta en 2018

La referencia se calculó sobre un observado distinto del nuestro de 2009 en adelante: el de la
compilación del Ministerio de Economía (dataset 41 de la Subsecretaría de Programación
Macroeconómica), que corre por debajo del primario y la brecha crece de 0,000% en 2009-2013 a
~1% desde 2019. Ese ~1% NO es un problema de la receta de X-13: está en el insumo, y ninguna
combinación de parámetros lo va a cerrar.

Por eso el barrido ordena por el error en 1996-2018, donde las dos fuentes coinciden y la
comparación mide de verdad la receta. El error del período completo se imprime al lado, para
ver cuánto mete el cambio de fuente — no para elegir con él.

Uso: python scripts/calibrar_hidrocarburos.py [petroleo|gas]
"""
import datetime as dt
import os
import subprocess
import sys
import tempfile

import openpyxl
import urllib3

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from etl.core import seasonal as S  # noqa: E402
from etl.datasets.hidrocarburos import config, load_history, source  # noqa: E402

# Último mes en que la fuente del histórico y la primaria coinciden (ver el docstring).
FIN_CALIBRACION = (2018, 12)


def load_ref(serie):
    """Excel de la serie -> {(año, mes): desest de referencia} (columna C)."""
    ws = openpyxl.load_workbook(load_history.XLSX[serie], data_only=True)[load_history.HOJA]
    ref = {}
    for fecha, _obs, desest in ws.iter_rows(min_row=1, max_row=load_history.FILAS,
                                            max_col=3, values_only=True):
        if fecha is None or desest is None:
            continue
        f = fecha.date() if isinstance(fecha, dt.datetime) else fecha
        ref[(f.year, f.month)] = float(desest)
    return ref


def build_series(serie):
    """Observada empalmada: Excel hasta 2008-12, Superset 2009→ (igual que la vista _actual)."""
    hist = {(d.year, d.month): v for d, v in load_history.read_series(
        str(load_history.XLSX[serie]))}
    sup = {(d.year, d.month): datos["total"] for d, datos in source.get_serie(serie).items()}
    dates, obs = [], []
    for k in sorted(set(hist) | set(sup)):
        v = sup.get(k) if k >= (2009, 1) else hist.get(k)
        if v is None:
            v = hist.get(k)
        if v is None:
            continue
        dates.append(dt.date(k[0], k[1], 1))
        obs.append(v)
    return dates, obs, load_ref(serie)


def run(x13, dates, obs, ref, mode, td, sma):
    wd = tempfile.mkdtemp(prefix="cal_hidro_")
    S._write_spc(os.path.join(wd, "serie.spc"), dates, obs, mode=mode, td=td, seasonalma=sma)
    subprocess.run([x13, "serie"], cwd=wd, capture_output=True, text=True, timeout=200)
    p = os.path.join(wd, "serie.d11")
    if not os.path.isfile(p):
        return None
    d11 = {(d.year, d.month): v for d, v in S._parse_d11(p)}
    comun = [k for k in ref if k in d11]
    if not comun:
        return None
    def err(ks):
        e = [abs(d11[k] - ref[k]) / abs(ref[k]) * 100 for k in ks]
        return (sum(e) / len(e), max(e)) if e else (float("nan"),) * 2
    cal = [k for k in comun if k <= FIN_CALIBRACION]
    return dict(mode=mode, td=td, sma=sma, arima=S._arima_model(wd, "serie"),
                cal_mean=err(cal)[0], cal_max=err(cal)[1],
                all_mean=err(comun)[0], all_max=err(comun)[1], n_cal=len(cal), n=len(comun))


def calibrar(x13, serie):
    dates, obs, ref = build_series(serie)
    print(f"\n===== {serie} =====")
    print(f"serie: {len(obs)} meses {dates[0]:%Y-%m}..{dates[-1]:%Y-%m} | ref: {len(ref)} | "
          f"ventana de calibracion: hasta {FIN_CALIBRACION[0]}-{FIN_CALIBRACION[1]:02d}")
    res = []
    for mode in ("add", "mult", "auto"):
        for td in ("td1coef", "td", "none"):
            for sma in ("s3x5", "s3x3"):
                try:
                    r = run(x13, dates, obs, ref, mode, td, sma)
                except Exception:
                    r = None
                if r:
                    res.append(r)
    res.sort(key=lambda r: r["cal_mean"])
    print(f"{'mode':5} {'td':8} {'sma':5} {'arima':18} "
          f"{'cal_mean%':>10} {'cal_max%':>9} | {'full_mean%':>11} {'full_max%':>10}")
    for r in res:
        print(f"{r['mode']:5} {r['td']:8} {r['sma']:5} {str(r['arima']):18} "
              f"{r['cal_mean']:10.4f} {r['cal_max']:9.4f} | "
              f"{r['all_mean']:11.4f} {r['all_max']:10.4f}")


def main():
    urllib3.disable_warnings()
    x13 = S._x13_binary()
    if not x13:
        sys.exit("x13as no encontrado (setear X13PATH)")
    series = sys.argv[1:] or list(config.TOTALES)
    for s in series:
        calibrar(x13, s)


if __name__ == "__main__":
    main()
