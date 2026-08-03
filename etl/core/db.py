"""Conexión a Postgres (Supabase) + insert/dedup append-only, genérico por dataset.

Modelo append-only compartido por las tres series: cada corrida inserta un snapshot
nuevo con su `ingested_at`; nunca se pisa un dato. Para no duplicar en cada corrida del
cron, sólo se inserta un (clave, estado) si no existe o si cambió algún valor respecto
del último snapshot.

Cada dataset describe su tabla con un `config` (ver `etl/datasets/<name>/config.py`):
  - `TABLE`       nombre de la tabla
  - `KEY_COLS`    columnas que identifican la fila además de `estado`
                  (p.ej. `["date"]` o `["serie", "date"]`)
  - `VALUE_COLS`  columnas de valor que definen un snapshot (la 1ª suele ser `valor`)

Resolución de conexión: 1) `DATABASE_URL` si está; 2) variables sueltas `PG*`; 3) `POSTGRES_*`
como fallback. No hay archivo `.env` (se llama a `load_dotenv()` por si existiera): las vars
salen del entorno — del `~/.bashrc` en interactivo y del crontab en el cron (host `10.0.16.3`,
db `data`, vía `POSTGRES_*`).
"""
from __future__ import annotations

import os

import psycopg2
from dotenv import load_dotenv

load_dotenv()


def get_conn():
    """Abre una conexión Postgres. Prioriza DATABASE_URL; cae a variables sueltas."""
    url = os.environ.get("DATABASE_URL")
    if url:
        return psycopg2.connect(url)
    host = os.environ.get("PGHOST") or os.environ.get("POSTGRES_HOST")
    if not host:
        raise RuntimeError(
            "Sin datos de conexión: no hay DATABASE_URL ni PGHOST/POSTGRES_HOST en el entorno. "
            "En el servidor esas variables (POSTGRES_*) las define el crontab; para correr a "
            "mano fuera del cron, exportalas en tu shell, p.ej.:\n"
            "  export POSTGRES_HOST=10.0.16.3 POSTGRES_DB=data POSTGRES_USER=postgres "
            "POSTGRES_PASSWORD=... POSTGRES_PORT=5432\n"
            "El .env (no versionado) lleva solo lo que NO es conexión (X13PATH, CEMENTO_PROXY)."
        )
    return psycopg2.connect(
        host=host,
        port=os.environ.get("PGPORT", os.environ.get("POSTGRES_PORT", "5432")),
        dbname=os.environ.get("PGDATABASE", os.environ.get("POSTGRES_DB", "postgres")),
        user=os.environ.get("PGUSER", os.environ.get("POSTGRES_USER", "postgres")),
        password=os.environ.get("PGPASSWORD") or os.environ.get("POSTGRES_PASSWORD", ""),
        # 'prefer' (default de libpq): usa SSL si el server lo ofrece y si no cae a no-SSL.
        # Sirve igual para Supabase (usa SSL) y para un Postgres local sin SSL. Setear
        # PGSSLMODE=require/disable en el .bashrc si se quiere forzar.
        sslmode=os.environ.get("PGSSLMODE", os.environ.get("POSTGRES_SSLMODE", "prefer")),
    )


def _key_where(key_cols: list[str], key_vals: list, estado: str | None):
    """Fragmento WHERE por (key_cols..., estado) y sus parámetros.

    `estado=None` consulta las filas históricas (estado IS NULL)."""
    parts, params = [], []
    for col, val in zip(key_cols, key_vals):
        parts.append(f"{col} = %s")
        params.append(val)
    if estado is None:
        parts.append("estado is null")
    else:
        parts.append("estado = %s")
        params.append(estado)
    return " and ".join(parts), params


def latest_values(conn, *, table, key_cols, key_vals, value_cols,
                  estado) -> dict | None:
    """Último snapshot (por ingested_at) de (clave, estado): dict {col: valor} o None."""
    where, params = _key_where(key_cols, key_vals, estado)
    sql = (f"select {', '.join(value_cols)} from {table} "
           f"where {where} order by ingested_at desc limit 1")
    with conn.cursor() as cur:
        cur.execute(sql, params)
        r = cur.fetchone()
    if r is None:
        return None
    return {c: (float(v) if v is not None else None) for c, v in zip(value_cols, r)}


def has_estado(conn, *, table, key_cols, key_vals, estado) -> bool:
    """True si ya existe al menos un snapshot de (clave, estado)."""
    where, params = _key_where(key_cols, key_vals, estado)
    with conn.cursor() as cur:
        cur.execute(f"select 1 from {table} where {where} limit 1", params)
        return cur.fetchone() is not None


def _changed(prev: dict | None, row: dict, value_cols: list[str], tol: float) -> bool:
    if prev is None:
        return True
    for c in value_cols:
        a, b = prev.get(c), row.get(c)
        if a is None and b is None:
            continue
        if a is None or b is None or abs(a - b) > tol:
            return True
    return False


def insert_if_changed(conn, *, table, key_cols, key_vals, value_cols, row,
                      estado, fuente, force: bool = False, tol: float = 1e-6,
                      extra: dict | None = None) -> str:
    """Inserta un snapshot de (clave, estado) sólo si es nuevo o cambió algún valor.

    `row` viene keyeado por los nombres de columna de la tabla. Idempotente salvo `force`.
    Devuelve un **status** para reportar de forma uniforme:
      - "saltado"     el valor principal (value_cols[0]) es None → no se inserta.
      - "sin_cambios" ya existía y no cambió (y sin force).
      - "nuevo"       no existía snapshot previo de (clave, estado) → se insertó.
      - "actualizado" existía pero cambió (o force) → se insertó snapshot nuevo.

    `extra` son columnas de contexto que se ESCRIBEN pero no se comparan para el dedup (no son
    valores de la serie). Lo usa `compras_granos` para la fecha de corte propia de cada bloque:
    si se comparara, un cambio sólo en esa fecha insertaría un snapshot con los mismos números.
    """
    if row.get(value_cols[0]) is None:
        return "saltado"
    prev = latest_values(conn, table=table, key_cols=key_cols, key_vals=key_vals,
                         value_cols=value_cols, estado=estado)
    if not force and not _changed(prev, row, value_cols, tol):
        return "sin_cambios"
    extra = extra or {}
    cols = list(key_cols) + list(value_cols) + ["estado", "fuente"] + list(extra)
    vals = list(key_vals) + [row.get(c) for c in value_cols] + [estado, fuente] + list(extra.values())
    placeholders = ", ".join(["%s"] * len(cols))
    sql = f"insert into {table} ({', '.join(cols)}) values ({placeholders})"
    with conn.cursor() as cur:
        cur.execute(sql, vals)
    conn.commit()
    return "actualizado" if prev is not None else "nuevo"


def bulk_insert(conn, *, table, cols, rows, page_size: int = 2000) -> int:
    """Inserta filas en lote (append-only, SIN dedup) en una sola transacción.

    Para backfills grandes donde no hay nada contra qué deduplicar (p.ej. la primera carga de un
    dataset con la tabla vacía). El camino normal `insert_if_changed` hace un SELECT + INSERT +
    COMMIT por fila: correcto para el incremental, inviable para ~200k filas contra una base
    remota. Devuelve la cantidad de filas insertadas.
    """
    from psycopg2.extras import execute_values

    rows = list(rows)
    if not rows:
        return 0
    sql = f"insert into {table} ({', '.join(cols)}) values %s"
    with conn.cursor() as cur:
        execute_values(cur, sql, rows, page_size=page_size)
    conn.commit()
    return len(rows)


def last_date(conn, *, table, where=None, where_params=()):
    """Último `date` cargado en la tabla (para ponerse al día en el incremental).

    `where` filtra opcionalmente (p.ej. excluir desestacionalizado, o por serie).
    Devuelve un `datetime.date` o None si la tabla está vacía.
    """
    sql = f"select max(date) from {table}"
    if where:
        sql += f" where {where}"
    with conn.cursor() as cur:
        cur.execute(sql, where_params)
        r = cur.fetchone()
    return r[0] if r else None
