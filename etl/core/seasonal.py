"""Desestacionalización Census X-13ARIMA-SEATS, llamando al binario directo.

Reutilizable entre ETLs: toma una serie mensual observada (1 valor por mes) desde una
vista, corre X-13 y hace UPSERT del resultado como estado 'desestacionalizado' (1 fila por
mes que se actualiza en cada corrida).

No depende de statsmodels: arma el .spc, ejecuta x13as y lee la tabla d11 (serie
desestacionalizada por X-11). Funciona con el binario "html" (x13ashtml) renombrado a
x13as, que es el que se consigue precompilado para Linux.

Requisitos: binario x13as accesible y `X13PATH` apuntando a su carpeta (o al binario).

Si falta X13PATH/el binario, NO rompe: avisa y saltea (devuelve "skipped"), así el ETL y
la demo en Windows siguen andando (la desest se corre aparte, p.ej. en una VM Linux).
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from datetime import date

from psycopg2.extras import Json

MIN_MESES = 36           # X-13 necesita varios años de historia
MAX_LINEA = 120          # X-13 corta las líneas de input a ~132 chars; dejamos margen


def _x13_binary():
    """Ruta al binario x13as a partir de X13PATH (carpeta o archivo), o None."""
    x13path = os.environ.get("X13PATH")
    if not x13path:
        return None
    if os.path.isfile(x13path):
        return x13path
    for name in ("x13as", "x13as.exe", "x13ashtml"):
        cand = os.path.join(x13path, name)
        if os.path.isfile(cand):
            return cand
    return None


def _es_contigua(dates) -> bool:
    """True si la lista de fechas (primer día de mes) es mensual sin huecos."""
    for a, b in zip(dates, dates[1:]):
        esperado_mes = a.month % 12 + 1
        esperado_anio = a.year + (1 if a.month == 12 else 0)
        if (b.year, b.month) != (esperado_anio, esperado_mes):
            return False
    return True


def _write_spc(path, dates, values, mode="auto", td="td1coef", seasonalma="s3x5"):
    """Escribe el .spc de X-13: regARIMA (modelo ARIMA auto + outliers) + trading-day + X-11.

    Los parámetros salen del cuadro por serie (series_desest.toml) y reproducen la referencia
    de cada serie (ver etl/core/desest_params.py). Componentes:
      - `transform`  none (aditivo) / log (multiplicativo) / auto (X-13 elige), según `mode`.
      - `regression`  ajuste por días hábiles. **Es por serie**: produccion `td1coef` (1 coef),
        cemento `td` (6 coef), soja `none` (sin ajuste). En modo auto se testea por AIC
        (`aictest`); si no, se fuerza como `variables`.
      - `automdl`  elige el modelo ARIMA; `outlier` detecta AO/LS/TC.
      - `x11{ seasonalma=<…> }`  filtro estacional (s3x5 es el estándar); leemos d11.

    `mode`:
      - 'add'   aditivo   -> transform=none (admite ceros, p.ej. produccion abril-2020).
      - 'mult'  multipl.   -> transform=log  (requiere serie estrictamente positiva).
      - 'auto'  X-13 elige -> transform=auto (decide add/mult por AIC).
    La elección viene del cuadro; el caller sólo la fuerza a 'add' si la serie tiene un <= 0.
    """
    y, m = dates[0].year, dates[0].month
    nums = [f"{v:.3f}" for v in values]
    # Envolver por ancho de línea (no por conteo fijo): series de valores grandes (p.ej. leche
    # en litros, ~9 dígitos) desbordan el límite de ~132 chars de X-13 y le parten un número.
    # X-13 lee formato libre, así que el agrupado no cambia su salida.
    bloques, cur = [], "  "
    for n in nums:
        add = n if cur == "  " else " " + n
        if cur != "  " and len(cur) + len(add) > MAX_LINEA:
            bloques.append(cur)
            cur = "  " + n
        else:
            cur += add
    if cur.strip():
        bloques.append(cur)
    data = "\n".join(bloques)
    transform = {"add": "none", "mult": "log", "auto": "auto"}[mode]
    # d10=factores estacionales, d11=serie desest, d12=tendencia, d13=irregular.
    saves = "save=(d10 d11 d12 d13)"
    sma = f"seasonalma={seasonalma} "
    x11_opts = (f"mode=add {sma}" if mode == "add" else sma) + saves
    # Trading-day: 'none' = sin bloque regression; en auto se testea por AIC, si no, fijo.
    if td == "none":
        reg = ""
    elif mode == "auto":
        reg = f"regression{{ aictest=({td}) }}\n"
    else:
        reg = f"regression{{ variables=({td}) }}\n"
    spc = (
        f'series{{ title="serie" start={y}.{m:02d} period=12\n'
        f' data=(\n{data}\n ) }}\n'
        f'transform{{ function={transform} }}\n'
        f'{reg}'
        f'automdl{{ }}\n'
        f'outlier{{ }}\n'
        f'x11{{ {x11_opts} }}\n'
    )
    with open(path, "w") as f:
        f.write(spc)


def _parse_d11(path):
    """Lee la tabla d11 -> lista de (date primer-día-de-mes, valor)."""
    out = []
    with open(path) as f:
        for ln in f:
            parts = ln.split()
            if len(parts) != 2:
                continue
            ym, val = parts
            if not (len(ym) == 6 and ym.isdigit()):
                continue  # saltea header y separador
            out.append((date(int(ym[:4]), int(ym[4:6]), 1), round(float(val), 3)))
    return out


_TAG_RE = re.compile(r"<[^>]*>")
# Modelo ARIMA: dos triples entre paréntesis, ej. (1 1 1)(0 1 1).
_MODEL_RE = re.compile(r"\(\s*\d+\s+\d+\s+\d+\s*\)\s*\(\s*\d+\s+\d+\s+\d+\s*\)")


def _arima_model(workdir, base):
    """Modelo ARIMA elegido por automdl, leído del `serie.html` (la build HTML no genera .udg).

    Ancla en 'Final automatic model choice' o 'ARIMA Model:' para tomar el modelo FINAL (no el
    'Preliminary model choice'). Devuelve el string normalizado, ej. '(1 1 1)(0 1 1)', o None.
    """
    path = os.path.join(workdir, base + ".html")
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8", errors="ignore") as f:
            text = _TAG_RE.sub("", f.read())
    except OSError:
        return None
    for label in ("Final automatic model choice", "ARIMA Model:"):
        idx = text.find(label)
        if idx != -1:
            m = _MODEL_RE.search(text, idx)
            if m:
                return re.sub(r"\s+", " ", m.group(0))
    return None


def x13_available() -> bool:
    """True si hay binario x13as resoluble (X13PATH seteado y existe)."""
    return _x13_binary() is not None


def _result(tag, status, *, n=0, mode=None, reason="", outdir=None) -> dict:
    return {"tag": tag, "status": status, "n": n,
            "mode": mode or "mult", "reason": reason, "outdir": outdir}


def deseasonalize(conn, *, table, source_view, conflict_cols=("date",),
                  extra_cols=None, where=None, where_params=(),
                  out_estado="desestacionalizado", fuente="census x13",
                  mode="auto", td="td1coef", seasonalma="s3x5", start=None,
                  keep_dir=None) -> dict:
    """Corre X-13 sobre la serie observada y hace UPSERT de la desestacionalizada.

    - `source_view`   vista con (date, valor) de la serie observada.
    - `where`/`where_params`  filtro opcional sobre la vista (p.ej. por `serie`).
    - `extra_cols`    columnas fijas a setear en cada fila insertada (p.ej. {"serie": ...}).
    - `mode`/`td`/`seasonalma`  parámetros de X-13 del cuadro por serie (series_desest.toml):
                      modo (add/mult/auto), trading-day (td1coef/td/none) y filtro estacional.
    - `conflict_cols` columnas del índice parcial único (target del ON CONFLICT).
    - `keep_dir`      si se pasa, guarda la salida completa de x13as (serie.html con el
                      modelo/factores/diagnósticos, + tablas d10/d11/d12/d13 y el .spc) en
                      `keep_dir/<tag>/` y NO la borra (para inspeccionar / ajustar la serie).

    No imprime: devuelve un dict {tag, status(ok|skipped|error), n, mode, reason, outdir}
    que el caller reporta de forma uniforme (ver `run_desest`).
    """
    extra_cols = dict(extra_cols or {})
    tag = "_".join(str(v) for v in extra_cols.values()) or table

    x13bin = _x13_binary()
    if not x13bin:
        return _result(tag, "skipped", reason="x13as no encontrado (setear X13PATH)")

    # 1. Serie observada (1 valor por mes) desde la vista.
    sql = f"select date, valor from {source_view}"
    if where:
        sql += f" where {where}"
    sql += " order by date"
    with conn.cursor() as cur:
        cur.execute(sql, where_params)
        rows = cur.fetchall()
    # Recorte opcional del histórico (evita un prefijo degenerado que cuelga a X-13); ver el
    # campo `start` del cuadro. El dato crudo pre-start queda igual en la tabla, solo no se ajusta.
    if start:
        rows = [r for r in rows if r[0] >= start]
    if len(rows) < MIN_MESES:
        return _result(tag, "skipped", reason=f"serie corta ({len(rows)} meses, min {MIN_MESES})")
    dates = [r[0] for r in rows]
    values = [float(r[1]) for r in rows]
    if not _es_contigua(dates):
        return _result(tag, "skipped", reason="la serie tiene huecos mensuales")

    # 2. Correr x13as en un directorio temporal.
    # El modo viene del cuadro; pero el X-11 mult/log no admite valores <= 0, así que si la
    # serie tiene algún cero/negativo (p.ej. produccion abril-2020, COVID) forzamos aditivo.
    forced_add = mode != "add" and any(v <= 0 for v in values)
    if forced_add:
        mode = "add"
    if keep_dir:
        workdir = os.path.join(keep_dir, tag)
        os.makedirs(workdir, exist_ok=True)
    else:
        workdir = tempfile.mkdtemp(prefix="x13_")
    base = "serie"
    _write_spc(os.path.join(workdir, base + ".spc"), dates, values, mode=mode,
               td=td, seasonalma=seasonalma)
    try:
        # 200s: el peor caso medido (jul-2026) es automotriz/produccion con 34s, seguido de
        # granos/mani con 28s; el resto corre en menos de 4s. El tiempo lo manda la dificultad
        # de ajuste de automdl, no el largo de la serie (mani: 282 obs / 28s vs aves: 545 obs /
        # 2s), así que una serie puede volverse lenta sin crecer. ~6x de margen sobre el peor caso.
        subprocess.run([x13bin, base], cwd=workdir, capture_output=True,
                       text=True, timeout=200)
    except Exception as e:
        return _result(tag, "error", mode=mode, reason=f"x13as no se pudo ejecutar: {e}")

    d11 = os.path.join(workdir, base + ".d11")
    if not os.path.isfile(d11):
        return _result(tag, "error", mode=mode,
                       reason=f"sin d11 (ver {workdir}/{base}_err.html)",
                       outdir=workdir if keep_dir else None)
    series = _parse_d11(d11)

    # Parámetros de la corrida X-13, para auditar por qué un valor puede no coincidir con otro
    # cálculo. Corremos regARIMA (modelo ARIMA automático + outliers) + X-11; el modelo que
    # eligió automdl se lee del .udg. Lo que más mueve la serie ajustada es el modo (mult/add).
    params = {
        "metodo": "x11",
        "modo": {"add": "aditivo", "mult": "multiplicativo", "auto": "auto"}[mode],
        "transform": {"add": "none", "mult": "log", "auto": "auto"}[mode],
        "regarima": True,        # preajuste regARIMA con modelo ARIMA automático (automdl)
        "automdl": True,
        "outliers": "auto",      # outlier{} detecta AO/LS/TC
        "trading_day": td,       # ajuste por días hábiles (por serie: td1coef / td / none)
        "seasonalma": seasonalma,  # filtro estacional (s3x5)
        "tabla": "d11",          # serie ajustada por X-11 que leemos
        "n_meses": len(rows),
    }
    if start:
        params["desde"] = start.isoformat()  # recorte del histórico aplicado (cuadro: start)
    arima = _arima_model(workdir, base)
    if arima:
        params["arima"] = arima   # modelo ARIMA elegido (ej. '(0 1 1)(0 1 1)')
    if forced_add:
        params["modo_motivo"] = "serie con algun valor <= 0 (el X-11 multiplicativo no lo admite)"
    params_json = Json(params)

    # 3. UPSERT: 1 fila por mes con estado=out_estado (se actualiza cada corrida).
    cols = list(extra_cols) + ["date", "valor", "estado", "fuente", "parametros"]
    placeholders = ", ".join(["%s"] * len(cols))
    target = ", ".join(conflict_cols)
    sql = (
        f"insert into {table} ({', '.join(cols)}) values ({placeholders}) "
        f"on conflict ({target}) where estado = '{out_estado}' "
        f"do update set valor = excluded.valor, ingested_at = now(), "
        f"parametros = excluded.parametros"
    )
    fixed = list(extra_cols.values())
    with conn.cursor() as cur:
        for d, val in series:
            cur.execute(sql, fixed + [d, val, out_estado, fuente, params_json])
    conn.commit()

    if not keep_dir:
        shutil.rmtree(workdir, ignore_errors=True)
    return _result(tag, "ok", n=len(series), mode=mode,
                   outdir=workdir if keep_dir else None)


def run_desest(conn, dataset: str, jobs) -> None:
    """Corre la desest de uno o más series y reporta el bloque `[dataset / desest]`.

    `jobs` = lista de (tag, kwargs_para_deseasonalize). X-13 nunca tumba el ETL: cualquier
    excepción se reporta como status=error.
    """
    from . import report  # import local: evita ciclo y solo se usa acá
    drep = report.DesestReport(dataset)
    for tag, kwargs in jobs:
        try:
            res = deseasonalize(conn, **kwargs)
        except Exception as e:  # DB caída, etc.
            res = _result(tag, "error", reason=str(e))
        drep.add(res)
    drep.summary()
