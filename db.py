"""Conexión a Postgres (Supabase) + lógica append-only de insert/dedup.

Prioriza DATABASE_URL (connection pooler de Supabase). Ver .env.example.
"""
from __future__ import annotations

import datetime as dt
import os

import psycopg2
from psycopg2.extras import execute_values
from dotenv import load_dotenv

load_dotenv()

# Columnas de valor que definen un snapshot (para comparar cambios).
VALUE_COLS = ["valor", "soja", "girasol", "lino", "mani", "algodon", "cartamo", "canola"]


def get_conn():
    """Devuelve una conexión psycopg2. Prioriza DATABASE_URL."""
    dsn = os.getenv("DATABASE_URL")
    if dsn:
        return psycopg2.connect(dsn)
    # Fallback a variables sueltas (opcional).
    return psycopg2.connect(
        host=os.getenv("PGHOST"),
        port=os.getenv("PGPORT", "6543"),
        dbname=os.getenv("PGDATABASE", "postgres"),
        user=os.getenv("PGUSER"),
        password=os.getenv("PGPASSWORD"),
    )


def latest_snapshot(conn, date: dt.date, estado: str | None) -> dict | None:
    """Último snapshot (por ingested_at) de un (date, estado). None si no existe."""
    if estado is None:
        where, params = "estado is null", [date]
    else:
        where, params = "estado = %s", [date, estado]
    sql = (
        f"select {', '.join(VALUE_COLS)} from molienda_granos "
        f"where date = %s and {where} order by ingested_at desc limit 1"
    )
    with conn.cursor() as cur:
        cur.execute(sql, params)
        r = cur.fetchone()
    if r is None:
        return None
    return {c: (float(v) if v is not None else None) for c, v in zip(VALUE_COLS, r)}


def _changed(prev: dict | None, row: dict) -> bool:
    if prev is None:
        return True
    for c in VALUE_COLS:
        if prev.get(c) != row.get(c):
            return True
    return False


def insert_if_changed(conn, date: dt.date, row: dict, estado: str | None,
                      fuente: str, force: bool = False) -> bool:
    """Inserta un snapshot nuevo de (date, estado) solo si no existe o cambió algún
    valor respecto del último. Devuelve True si insertó. Idempotente."""
    if not force:
        prev = latest_snapshot(conn, date, estado)
        if not _changed(prev, row):
            return False
    cols = ["date"] + VALUE_COLS + ["estado", "fuente"]
    vals = [date] + [row.get(c) for c in VALUE_COLS] + [estado, fuente]
    placeholders = ", ".join(["%s"] * len(cols))
    sql = f"insert into molienda_granos ({', '.join(cols)}) values ({placeholders})"
    with conn.cursor() as cur:
        cur.execute(sql, vals)
    conn.commit()
    return True


def upsert_desest(conn, date: dt.date, valor: float, fuente: str = "census x13") -> None:
    """UPSERT de la fila desestacionalizada del mes (1 por mes, índice parcial único)."""
    sql = (
        "insert into molienda_granos (date, valor, estado, fuente) "
        "values (%s, %s, 'desestacionalizado', %s) "
        "on conflict (date) where estado = 'desestacionalizado' "
        "do update set valor = excluded.valor, fuente = excluded.fuente, "
        "ingested_at = now()"
    )
    with conn.cursor() as cur:
        cur.execute(sql, [date, valor, fuente])
    conn.commit()


def upsert_desest_batch(conn, rows: list[tuple[dt.date, float]],
                        fuente: str = "census x13") -> int:
    """UPSERT en lote de las filas desestacionalizadas (un solo commit).

    rows = [(date, valor), ...]. Mucho más rápido que llamar upsert_desest por mes
    sobre el pooler (1 roundtrip en vez de N commits)."""
    if not rows:
        return 0
    sql = (
        "insert into molienda_granos (date, valor, estado, fuente) values %s "
        "on conflict (date) where estado = 'desestacionalizado' "
        "do update set valor = excluded.valor, fuente = excluded.fuente, "
        "ingested_at = now()"
    )
    values = [(d, v, fuente) for d, v in rows]
    with conn.cursor() as cur:
        execute_values(cur, sql, values,
                       template="(%s, %s, 'desestacionalizado', %s)")
    conn.commit()
    return len(rows)


def fetch_observed_series(conn) -> list[tuple[dt.date, float]]:
    """Serie observada (vista actual) para alimentar X-13: [(date, valor), ...]."""
    with conn.cursor() as cur:
        cur.execute(
            "select date, valor from molienda_granos_actual "
            "where valor is not null order by date"
        )
        return [(d, float(v)) for d, v in cur.fetchall()]
