"""Corrida X-13 sin parametros: SOLO la serie.

Arma un .spc minimo con un unico bloque `series{...}` (sin transform, sin regression,
sin automdl, sin outlier, sin x11/seats) y ejecuta X-13 para ver que resultado da.

Si X-13 no emite una tabla desestacionalizada, lo reporta como resultado esperado
de una corrida sin bloque de descomposicion.

Uso (server, venv activado, X13PATH seteado):
    python scripts/correr_sin_params.py [xlsx] [workdir]

Ejemplo:
    python scripts/correr_sin_params.py autos_prod.xlsx /tmp/sin_params
"""
from __future__ import annotations

import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(__file__))
import calibrar as C  # noqa: E402


DEFAULT_WORKDIR = "/tmp/sin_params"
CANDIDATE_TABLES = (".d11", ".s11", ".d12", ".d13", ".d10")


def build_spc_series_only(dates, values) -> str:
    y, m = dates[0].year, dates[0].month
    nums = [f"{x:.4f}" for x in values]
    blocks = ["  " + " ".join(nums[i:i + 10]) for i in range(0, len(nums), 10)]
    data = "\n".join(blocks)
    return (
        f'series{{ title="serie" start={y}.{m:02d} period=12\n'
        f' data=(\n{data}\n ) }}\n'
    )


def run_series_only(binary, dates, values, workdir):
    os.makedirs(workdir, exist_ok=True)

    spc_path = os.path.join(workdir, "serie.spc")
    with open(spc_path, "w") as f:
        f.write(build_spc_series_only(dates, values))

    proc = subprocess.run(
        [binary, "serie"],
        cwd=workdir,
        capture_output=True,
        text=True,
        timeout=180,
    )

    series = None
    used_table = None
    for ext in CANDIDATE_TABLES:
        p = os.path.join(workdir, "serie" + ext)
        if os.path.isfile(p):
            parsed = C.parse_table(p)
            if parsed:
                series = parsed
                used_table = ext
                break

    return proc, series, used_table


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv

    xlsx = argv[0] if argv else os.path.join(os.path.dirname(__file__), "..", "autos_prod.xlsx")
    xlsx = os.path.abspath(xlsx)
    workdir = argv[1] if len(argv) > 1 else DEFAULT_WORKDIR

    if not os.path.isfile(xlsx):
        sys.exit(f"No encontre el xlsx: {xlsx}")

    binary = C.find_binary()
    dates, orig, desest = C.read_juan(xlsx)
    juan_apr = next((s for d, s in zip(dates, desest) if (d.year, d.month) == C.APR2020), None)

    print("=== X-13 SIN PARAMS (solo series) ===")
    print(f"binario: {binary}")
    print(f"xlsx: {xlsx}")
    print(f"workdir: {workdir}")
    print(f"meses: {len(dates)} ({dates[0]}..{dates[-1]})")

    try:
        proc, series, used_table = run_series_only(binary, dates, orig, workdir)
    except Exception as e:
        sys.exit(f"Error ejecutando X-13: {e}")

    if proc.returncode != 0:
        print(f"WARN: x13as devolvio codigo {proc.returncode}")

    model = C.arima_from_html(workdir)

    print(f"modelo detectado en html: {model or 'N/D'}")

    if not series:
        print("\n=== Resultado ===")
        print("X-13 con 'solo series' no genero una tabla parseable de salida.")
        print("No hay d11/s11 para comparar contra la columna desest_juan.")
        print("Eso es lo esperable cuando se corre sin bloques de modelo/descomposicion.")
    else:
        comp = C.compare(series, dates, desest)
        print(f"tabla usada para comparar: {used_table}")

        if comp:
            print("\n=== Comparacion vs desest_juan ===")
            print(f"err_medio   : {comp['mean']:.2f}")
            print(f"err_exCOVID : {comp['mean_ex']:.2f}")
            print(f"err_max     : {comp['mx']:.2f} @ {comp['mx_at']}")
            print(f"abr2020     : {comp['apr']:.2f} (juan={juan_apr:.2f})")
        else:
            print("No se pudo calcular comparacion (sin meses en comun con referencia).")

    print("\nArchivos para mostrar al jefe:")
    print(f"- SPC sin params: {os.path.join(workdir, 'serie.spc')}")
    print(f"- Reporte X-13  : {os.path.join(workdir, 'serie.html')}")


if __name__ == "__main__":
    main()
