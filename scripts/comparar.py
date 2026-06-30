"""Compara la desest de Juan vs la nuestra (MISMO método, MISMO span de datos).

Corre x13as con la config que reproduce la referencia del jefe (aditivo + automdl + td1coef +
outlier + seasonalma=s3x5) sobre los ORIGINALES de Juan (mismos 388 meses, hasta 2026-04) y
escribe un CSV lado a lado: mes | original | desest_juan | desest_nuestro | diferencia.
Sirve para mostrarle a Juan que "da igual" y para entregarle el .spc exacto que usamos.

Uso (server, venv activado, X13PATH seteado):
    python scripts/comparar.py [xlsx] [csv_salida]
Deja el .spc usado en /tmp/comparar_prod/serie.spc (es, literal, cómo llamamos a X-13).
"""
from __future__ import annotations

import csv
import os
import statistics as st
import sys

sys.path.insert(0, os.path.dirname(__file__))
import calibrar as C  # noqa: E402  (reusa read_juan/build_spc/run_variant/find_binary)

# Config que reproduce EXACTO la referencia del jefe (ver scripts/calibrar.py, variante ganadora).
WIN = dict(transform="none", model="automdl", outlier=True, mode="add",
           reg="td1coef", seasonalma="s3x5")
WORKDIR = "/tmp/comparar_prod"


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    xlsx = os.path.abspath(argv[0] if argv else
                           os.path.join(os.path.dirname(__file__), "..", "autos_prod.xlsx"))
    out = argv[1] if len(argv) > 1 else "/tmp/comparacion_produccion.csv"
    binary = C.find_binary()
    dates, orig, juan = C.read_juan(xlsx)
    series = C.run_variant(binary, dates, orig, WIN, WORKDIR)
    if not series:
        sys.exit(f"x13as no produjo salida (ver {WORKDIR}/serie_err.html)")

    rows, errs = [], []
    for d, o, j in zip(dates, orig, juan):
        ours = series.get((d.year, d.month))
        diff = (ours - j) if (ours is not None and j is not None) else None
        rows.append([f"{d.year}-{d.month:02d}", o, j, ours, diff])
        if diff is not None:
            errs.append(abs(diff))
    with open(out, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["mes", "original", "desest_juan", "desest_nuestro", "diferencia"])
        w.writerows(rows)
    print(f"CSV escrito: {out}")
    print(f"meses comparados = {len(errs)}  err_medio = {st.mean(errs):.3f}  err_max = {max(errs):.3f}")
    print(f"spec exacto (cómo llamamos a X-13): {WORKDIR}/serie.spc")


if __name__ == "__main__":
    main()
