"""Desestacionalización X-13ARIMA-SEATS reutilizable.

Tras el ETL, corre X-13 sobre la serie observada (vista molienda_granos_actual) y
hace UPSERT de estado='desestacionalizado', fuente='census x13' (1 fila por mes).

No usa statsmodels: arma un .spc, ejecuta el binario `x13as` (path en X13PATH) y lee
la tabla d11 (formato `YYYYMM  +0.xxxE+03`).

Degradación elegante: si no hay X13PATH / binario, saltea con aviso (no rompe el ETL).

Binario Linux precompilado (renombrar a x13as):
  curl -L -o x13as https://raw.githubusercontent.com/x13org/x13prebuilt/master/v1.1.57/linux/64/x13ashtml
  chmod +x x13as
  # en .env:  X13PATH=<carpeta del binario>
"""
from __future__ import annotations

import datetime as dt
import os
import re
import subprocess
import tempfile

import db

VALUES_PER_LINE = 10  # límite de 132 chars por línea del .spc


def _find_binary() -> str | None:
    """Resuelve el binario x13as desde X13PATH (carpeta o ruta directa)."""
    p = os.getenv("X13PATH")
    if not p:
        return None
    if os.path.isfile(p):
        return p
    for name in ("x13as", "x13as.exe", "x13ashtml"):
        cand = os.path.join(p, name)
        if os.path.isfile(cand):
            return cand
    return None


def _contiguous_tail(series: list[tuple[dt.date, float]]) -> list[tuple[dt.date, float]]:
    """Devuelve el tramo mensual contiguo más largo que termina en el último dato."""
    if not series:
        return series
    out = [series[-1]]
    for date, val in reversed(series[:-1]):
        nxt = out[0][0]
        prev_month = (nxt.replace(day=1) - dt.timedelta(days=1)).replace(day=1)
        if date == prev_month:
            out.insert(0, (date, val))
        else:
            break
    return out


def _build_spc(series: list[tuple[dt.date, float]]) -> str:
    start = series[0][0]
    lines = []
    for i in range(0, len(series), VALUES_PER_LINE):
        chunk = series[i:i + VALUES_PER_LINE]
        lines.append(" ".join(f"{v:.1f}" for _, v in chunk))
    data = "\n".join(lines)
    return (
        "series{\n"
        f"  title=\"molienda\"\n"
        f"  start={start.year}.{start.month:02d}\n"
        "  period=12\n"
        "  data=(\n"
        f"{data}\n"
        "  )\n"
        "}\n"
        "x11{ save=(d11) }\n"
    )


def _read_d11(path: str) -> list[tuple[dt.date, float]]:
    out: list[tuple[dt.date, float]] = []
    with open(path, "r") as f:
        for line in f:
            m = re.match(r"^\s*(\d{4})(\d{2})\s+([\-+]?[\d.]+(?:[eE][\-+]?\d+)?)\s*$", line)
            if not m:
                continue
            y, mo, val = int(m.group(1)), int(m.group(2)), float(m.group(3))
            out.append((dt.date(y, mo, 1), val))
    return out


def run_desest(conn) -> None:
    """Corre X-13 sobre la serie observada y UPSERTea la serie desestacionalizada."""
    binary = _find_binary()
    if not binary:
        print("[desest] X13PATH no seteado o binario no encontrado; salteado.")
        return

    series = _contiguous_tail(db.fetch_observed_series(conn))
    if len(series) < 36:  # X-13 necesita >= 3 años
        print(f"[desest] serie demasiado corta ({len(series)} meses); salteado.")
        return

    with tempfile.TemporaryDirectory() as tmp:
        # x13as quiere el basename del .spc (no la ruta completa) y se corre en cwd=tmp.
        name = "serie"
        with open(os.path.join(tmp, name + ".spc"), "w") as f:
            f.write(_build_spc(series))
        try:
            proc = subprocess.run(
                [binary, name], cwd=tmp, capture_output=True, text=True, timeout=120,
            )
        except subprocess.TimeoutExpired:
            print("[desest] x13as excedió el timeout; salteado.")
            return
        d11 = os.path.join(tmp, name + ".d11")
        if not os.path.isfile(d11):
            tail = (proc.stdout or "")[-400:] + (proc.stderr or "")[-400:]
            print(f"[desest] x13as no generó d11 (rc={proc.returncode}); salteado.\n{tail}")
            return
        values = _read_d11(d11)

    for date, val in values:
        db.upsert_desest(conn, date, float(val))
    print(f"[desest] UPSERT de {len(values)} meses desestacionalizados "
          f"({values[0][0]:%Y-%m}..{values[-1][0]:%Y-%m}).")


if __name__ == "__main__":
    conn = db.get_conn()
    try:
        run_desest(conn)
    finally:
        conn.close()
