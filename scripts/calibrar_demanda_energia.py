"""Calibra X-13 para demanda no residencial contra energia.xlsx (col desest).

Serie observada = energia.xlsx valor (2005..2022) + CAMMESA no_residencial (2023→, GWh, se
baja en vivo con la fuente del dataset). Barre mode x td x seasonalma, corre x13as y compara
el d11 contra la referencia (energia.xlsx col desest).

add + td1coef + s3x5 reproduce la referencia con error ~0 (medio 0.0000%, máx 0.0001%).

Uso: python scripts/calibrar_demanda_energia.py
"""
import datetime as dt
import os
import subprocess
import sys
import tempfile

import openpyxl

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from etl.core import seasonal as S  # noqa: E402
from etl.datasets.demanda_energia import config, source  # noqa: E402

XLSX = os.path.join(os.path.dirname(__file__), os.pardir,
                    "etl", "datasets", "demanda_energia", "data", "energia.xlsx")


def load_ref():
    """energia.xlsx -> (valor observado 2005..2022, desest de referencia)."""
    ws = openpyxl.load_workbook(XLSX, data_only=True).active
    valor, ref = {}, {}
    for row in list(ws.iter_rows(values_only=True))[1:]:
        d, v, de = row
        if not isinstance(d, dt.datetime):
            continue
        k = (d.year, d.month)
        if v not in (None, "", 0):
            valor[k] = float(v)
        if de not in (None, ""):
            ref[k] = float(de)
    return valor, ref


def build_series():
    """Serie observada empalmada: energia hasta 2022, CAMMESA 2023→."""
    valor, ref = load_ref()
    data, _ = source.get_latest()
    cam = {(d.year, d.month): v for d, v in data[config.MAIN_SERIE].items()}
    dates, obs = [], []
    for k in sorted(set(valor) | set(cam)):
        v = cam[k] if (k[0] >= 2023 and k in cam) else valor.get(k)
        if v is None:
            continue
        dates.append(dt.date(k[0], k[1], 1))
        obs.append(v)
    return dates, obs, ref


def run(x13, dates, obs, ref, mode, td, sma):
    wd = tempfile.mkdtemp(prefix="cal_energia_")
    S._write_spc(os.path.join(wd, "serie.spc"), dates, obs, mode=mode, td=td, seasonalma=sma)
    subprocess.run([x13, "serie"], cwd=wd, capture_output=True, text=True, timeout=120)
    p = os.path.join(wd, "serie.d11")
    if not os.path.isfile(p):
        return None
    d11 = {(d.year, d.month): v for d, v in S._parse_d11(p)}
    common = [k for k in ref if k in d11]
    if not common:
        return None
    pct = [abs(d11[k] - ref[k]) / abs(ref[k]) * 100 for k in common]
    return dict(mode=mode, td=td, sma=sma, arima=S._arima_model(wd, "serie"),
                meanpct=sum(pct) / len(pct), maxpct=max(pct))


def main():
    x13 = S._x13_binary()
    if not x13:
        sys.exit("x13as no encontrado (setear X13PATH)")
    dates, obs, ref = build_series()
    print(f"serie: {len(obs)} meses {dates[0]:%Y-%m}..{dates[-1]:%Y-%m} | ref: {len(ref)}")
    results = []
    for mode in ("add", "mult", "auto"):
        for td in ("td1coef", "td", "none"):
            for sma in ("s3x5", "s3x3"):
                try:
                    r = run(x13, dates, obs, ref, mode, td, sma)
                except Exception:
                    r = None
                if r:
                    results.append(r)
    results.sort(key=lambda r: r["meanpct"])
    print(f"\n{'mode':5} {'td':8} {'sma':5} {'arima':18} {'meanpct%':>9} {'maxpct%':>9}")
    for r in results:
        print(f"{r['mode']:5} {r['td']:8} {r['sma']:5} {str(r['arima']):18} "
              f"{r['meanpct']:9.4f} {r['maxpct']:9.4f}")


if __name__ == "__main__":
    main()
