"""Harness de calibración del X-13 contra la referencia de Juan (produccion).

Objetivo: encontrar qué configuración de X-13 (modo, modelo, outliers, método)
reproduce los valores desestacionalizados de Juan a partir de los originales.

Lee `autos_prod.xlsx` (3 columnas sin header: fecha | original | desest_juan), y para
cada variante de spec corre el binario x13as sobre los ORIGINALES de Juan, lee la serie
ajustada y la compara contra la columna desest_juan (error max/medio + el valor puntual
de abril-2020, que es el "tell": Juan da -1914 ahí => aditivo y sin outlier).

Uso (en el server, con el venv activado y X13PATH seteado):
    python scripts/calibrar.py [ruta_al_xlsx]

Necesita: openpyxl (ya está en el venv) y el binario x13as (vía X13PATH o ~/x13).
NO toca la base de datos. Deja la salida de cada variante en /tmp/calibrar/<n>/ para inspeccionar.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from datetime import date

import openpyxl

OUT_BASE = "/tmp/calibrar"
APR2020 = (2020, 4)
_MODEL_RE = re.compile(r"\(\s*\d+\s+\d+\s+\d+\s*\)\s*\(\s*\d+\s+\d+\s+\d+\s*\)")
_TAG_RE = re.compile(r"<[^>]*>")


def find_binary() -> str:
    """Ruta al binario x13as: X13PATH (archivo o carpeta) o ~/x13/x13as/x13as_html."""
    p = os.environ.get("X13PATH")
    cands = []
    if p:
        if os.path.isfile(p):
            return p
        cands += [os.path.join(p, n) for n in ("x13as_html", "x13as", "x13ashtml")]
    cands.append(os.path.expanduser("~/x13/x13as/x13as_html"))
    for c in cands:
        if os.path.isfile(c):
            return c
    sys.exit(f"No encontré el binario x13as. Seteá X13PATH. Probé: {cands}")


def read_juan(path: str):
    """(dates, original, desest_juan) desde el xlsx (col A=fecha, B=original, C=desest)."""
    wb = openpyxl.load_workbook(path, data_only=True)
    ws = wb[wb.sheetnames[0]]
    dates, orig, des = [], [], []
    for d, o, s in ws.iter_rows(values_only=True):
        if d is None or o is None:
            continue
        dd = d.date() if hasattr(d, "date") else d
        dates.append(date(dd.year, dd.month, 1))
        orig.append(float(o))
        des.append(float(s) if s is not None else None)
    wb.close()
    return dates, orig, des


def interp_nonpositive(values):
    """Reemplaza valores <= 0 por interpolación lineal de vecinos positivos (para log/mult)."""
    v = list(values)
    n = len(v)
    for i in range(n):
        if v[i] is None or v[i] <= 0:
            j = i - 1
            while j >= 0 and (v[j] is None or v[j] <= 0):
                j -= 1
            k = i + 1
            while k < n and (values[k] is None or values[k] <= 0):
                k += 1
            left = v[j] if j >= 0 else None
            right = values[k] if k < n else None
            if left is not None and right is not None:
                v[i] = left + (right - left) * (i - j) / (k - j)
            elif left is not None:
                v[i] = left
            elif right is not None:
                v[i] = right
            else:
                v[i] = 1.0
    return v


def build_spc(dates, values, opt) -> str:
    y, m = dates[0].year, dates[0].month
    nums = [f"{x:.4f}" for x in values]
    blocks = ["  " + " ".join(nums[i:i + 10]) for i in range(0, len(nums), 10)]
    data = "\n".join(blocks)
    lines = [f'series{{ title="serie" start={y}.{m:02d} period=12\n data=(\n{data}\n ) }}']
    if opt.get("transform"):
        lines.append(f'transform{{ function={opt["transform"]} }}')
    model = opt.get("model")
    if model == "automdl":
        lines.append("automdl{ }")
    elif model:
        lines.append(f"arima{{ model={model} }}")
    if opt.get("outlier"):
        lines.append("outlier{ }")
    if opt.get("decomp", "x11") == "x11":
        mode = opt.get("mode")
        lines.append(f'x11{{ {("mode=" + mode + " ") if mode else ""}save=(d11) }}')
    else:
        lines.append("seats{ save=(s11) }")
    return "\n".join(lines) + "\n"


def parse_table(path):
    """Lee un save table de x13as (lineas 'yyyymm valor') -> dict {(y,m): valor}."""
    out = {}
    with open(path) as f:
        for ln in f:
            parts = ln.split()
            if len(parts) < 2:
                continue
            ym = parts[0]
            if not (len(ym) == 6 and ym.isdigit()):
                continue
            try:
                out[(int(ym[:4]), int(ym[4:6]))] = float(parts[1])
            except ValueError:
                pass
    return out


def arima_from_html(workdir):
    p = os.path.join(workdir, "serie.html")
    if not os.path.isfile(p):
        return None
    with open(p, encoding="utf-8", errors="ignore") as f:
        text = _TAG_RE.sub("", f.read())
    for label in ("Final automatic model choice", "ARIMA Model:"):
        i = text.find(label)
        if i != -1:
            mm = _MODEL_RE.search(text, i)
            if mm:
                return re.sub(r"\s+", " ", mm.group(0))
    return None


def run_variant(binary, dates, values, opt, workdir):
    os.makedirs(workdir, exist_ok=True)
    with open(os.path.join(workdir, "serie.spc"), "w") as f:
        f.write(build_spc(dates, values, opt))
    subprocess.run([binary, "serie"], cwd=workdir, capture_output=True,
                   text=True, timeout=180)
    ext = ".d11" if opt.get("decomp", "x11") == "x11" else ".s11"
    p = os.path.join(workdir, "serie" + ext)
    if not os.path.isfile(p):
        return None
    return parse_table(p)


def compare(model_series, dates, desest):
    """Devuelve (max_err, mean_err, valor_abr2020) comparando contra Juan."""
    errs = []
    apr = None
    for d, s in zip(dates, desest):
        if s is None:
            continue
        got = model_series.get((d.year, d.month))
        if got is None:
            continue
        errs.append(abs(got - s))
        if (d.year, d.month) == APR2020:
            apr = got
    if not errs:
        return None, None, apr
    return max(errs), sum(errs) / len(errs), apr


GRID = [
    dict(label="X11 puro ADITIVO, sin outlier", mode="add", positive=False),
    dict(label="X11 puro ADITIVO, sin outlier (mode auto)", mode=None, positive=False),
    dict(label="X11 puro ADITIVO, CON outlier", transform="none", outlier=True, mode="add", positive=False),
    dict(label="regARIMA(automdl) ADITIVO, sin outlier", transform="none", model="automdl", mode="add", positive=False),
    dict(label="regARIMA(automdl) ADITIVO, CON outlier  [~actual]", transform="none", model="automdl", outlier=True, mode="add", positive=False),
    dict(label="ARIMA(0 1 1)(0 1 1) ADITIVO, sin outlier", transform="none", model="(0 1 1)(0 1 1)", mode="add", positive=False),
    dict(label="X11 puro MULT, sin outlier (cero interp)", mode="mult", positive=True),
    dict(label="regARIMA(automdl) MULT, sin outlier (cero interp)", transform="log", model="automdl", positive=True),
    dict(label="regARIMA(automdl) MULT, CON outlier (cero interp)", transform="log", model="automdl", outlier=True, positive=True),
    dict(label="SEATS ADITIVO automdl, sin outlier", transform="none", model="automdl", decomp="seats", positive=False),
    dict(label="SEATS MULT automdl, sin outlier (cero interp)", transform="log", model="automdl", decomp="seats", positive=True),
]


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    xlsx = argv[0] if argv else os.path.join(os.path.dirname(__file__), "..", "autos_prod.xlsx")
    xlsx = os.path.abspath(xlsx)
    if not os.path.isfile(xlsx):
        sys.exit(f"No encontré el xlsx: {xlsx}")
    binary = find_binary()
    dates, orig, desest = read_juan(xlsx)
    juan_apr = next((s for d, s in zip(dates, desest) if (d.year, d.month) == APR2020), None)
    print(f"binario: {binary}")
    print(f"datos: {len(dates)} meses {dates[0]}..{dates[-1]} | desest_juan abr-2020 = {juan_apr}")
    print(f"salidas en: {OUT_BASE}/<n>/\n")

    results = []
    for i, opt in enumerate(GRID):
        workdir = os.path.join(OUT_BASE, str(i))
        values = interp_nonpositive(orig) if opt.get("positive") else orig
        try:
            series = run_variant(binary, dates, values, opt, workdir)
        except Exception as e:
            print(f"[{i}] {opt['label']:50} ERROR: {e}")
            continue
        if series is None:
            print(f"[{i}] {opt['label']:50} sin salida (ver {workdir}/serie_err.html)")
            continue
        mx, mn, apr = compare(series, dates, desest)
        arima = arima_from_html(workdir) if opt.get("model") == "automdl" else (opt.get("model") or "x11-puro")
        results.append((mn, mx, apr, opt["label"], arima, i))

    print("\n==== RESULTADOS (ordenados por error medio; menor = mejor match a Juan) ====")
    print(f"{'err_medio':>12} {'err_max':>12} {'abr2020':>10}  modelo            variante")
    for mn, mx, apr, label, arima, i in sorted(results, key=lambda r: r[0]):
        aprs = f"{apr:10.0f}" if apr is not None else "     n/a"
        print(f"{mn:12.2f} {mx:12.2f} {aprs}  {str(arima):16}  [{i}] {label}")

    if results:
        best = min(results, key=lambda r: r[0])
        print(f"\nMEJOR: [{best[5]}] {best[3]}  (err_medio={best[0]:.2f}, abr2020={best[2]:.0f} vs juan={juan_apr:.0f})")
        print(f"Mirá el spc/reporte en {OUT_BASE}/{best[5]}/serie.spc  y  serie.html")


if __name__ == "__main__":
    main()
