"""Corre x13as SIN PARÁMETROS (solo la serie) y lo compara con el método del pipeline.

Juan pidió ver qué da x13as cuando se le pasa "solo la serie, sin parámetros". Este script
corre la MISMA serie (produccion de autos_prod.xlsx por default) con niveles crecientes de
config y muestra, para cada uno, el .spc EXACTO que se ejecuta, el modelo ARIMA que estima
x13as, y el error contra la referencia del jefe:

  1. SIN PARAMS      -> series{} + x11{}            (x13as usa TODOS los defaults)
  2. + automdl       -> series{} + automdl{} + x11{}   (x13as ESTIMA el modelo ARIMA solo)
  3. + automdl+outlier
  4. NUESTRO METODO  -> transform + td1coef + automdl + outlier + x11{seasonalma=s3x5}

Nota produccion: tiene un 0 (abril-2020, COVID). El X-11 por default es MULTIPLICATIVO y no
admite ceros, así que "sin params puro" no corre; el único ajuste forzado por el dato (no una
preferencia de método) es `mode=add`. El script lo detecta solo y lo aclara.

Uso (server, venv, X13PATH):  python scripts/sin_params.py [xlsx]
Deja el .spc/.html de cada corrida en /tmp/sin_params/<n>/ para inspeccionar.
"""
from __future__ import annotations

import os
import statistics as st
import sys

sys.path.insert(0, os.path.dirname(__file__))
import calibrar as C  # noqa: E402  (reusa read_juan/build_spc/run_variant/find_binary/arima_from_html)

OUT_BASE = "/tmp/sin_params"


def spec_lines(dates, values, opt):
    """Las líneas del .spc que NO son datos (para mostrar 'el llamado')."""
    out = []
    for ln in C.build_spc(dates, values, opt).splitlines():
        s = ln.strip()
        if s and not s[0].isdigit() and not s.startswith("data="):
            out.append(s)
    return [l for l in out if l not in ("data=(", ") }") and not l.startswith(str(values[0]))]


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    xlsx = os.path.abspath(argv[0] if argv else
                           os.path.join(os.path.dirname(__file__), "..", "autos_prod.xlsx"))
    binary = C.find_binary()
    dates, orig, juan = C.read_juan(xlsx)

    has0 = any(v <= 0 for v in orig)                 # produccion: True (0 de abril-2020)
    mode = "add" if has0 else None                    # forzado por el dato, no por método
    tf = "none" if has0 else None
    variantes = [
        ("SIN PARAMS (solo serie + x11 default)", dict(mode=mode)),
        ("+ automdl  (x13 estima el modelo)", dict(mode=mode, transform=tf, model="automdl")),
        ("+ automdl + outlier", dict(mode=mode, transform=tf, model="automdl", outlier=True)),
        ("NUESTRO metodo del pipeline", dict(mode=mode, transform=tf, model="automdl",
                                             outlier=True, reg="td1coef", seasonalma="s3x5")),
    ]

    print(f"binario: {binary}")
    print(f"serie: {len(dates)} meses {dates[0]}..{dates[-1]} | tiene cero(s): {has0}"
          f"{'  -> mode=add forzado por el 0' if has0 else ''}")
    print(f"salidas en {OUT_BASE}/<n>/\n")

    results = []
    for i, (label, opt) in enumerate(variantes):
        workdir = os.path.join(OUT_BASE, str(i))
        print(f"===== [{i}] {label} =====")
        print("  .spc (el llamado):", " | ".join(spec_lines(dates, orig, opt)))
        try:
            series = C.run_variant(binary, dates, orig, opt, workdir)
        except Exception as e:
            print(f"  ERROR: {e}\n"); continue
        if not series:
            print(f"  sin salida (x13as no produjo d11; ver {workdir}/serie_err.html)\n")
            results.append((label, None, None, None, i)); continue
        arima = C.arima_from_html(workdir)
        errs = [abs(series[(d.year, d.month)] - j)
                for d, j in zip(dates, juan)
                if j is not None and (d.year, d.month) in series]
        me = st.mean(errs) if errs else None
        apr = series.get((2020, 4))
        print(f"  modelo estimado: {arima or '(sin ARIMA: X-11 puro)'}")
        if me is not None:
            print(f"  err_medio vs Juan = {me:.1f}   |   abril-2020 = {apr:.0f}  (Juan = -1914)\n")
        results.append((label, me, apr, arima, i))

    print("==== RESUMEN (error medio vs la referencia de Juan) ====")
    print(f"{'err_medio':>10} {'abr2020':>9}  modelo          variante")
    for label, me, apr, arima, i in results:
        mes = f"{me:10.1f}" if me is not None else "   (falló)"
        aprs = f"{apr:9.0f}" if apr is not None else "      n/a"
        print(f"{mes} {aprs}  {str(arima):14}  [{i}] {label}")


if __name__ == "__main__":
    main()
