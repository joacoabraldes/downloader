"""Aplica el schema.sql de cada dataset a la base apuntada por DATABASE_URL.

Idempotente: los DDL usan `create table if not exists` / `create or replace view`.
Uso: `python -m etl init-db [granos cemento automotriz]` (sin args = todos).
"""
from __future__ import annotations

import argparse
from pathlib import Path

from etl.core import db

DATASETS_DIR = Path(__file__).parent / "datasets"
UNIFIED_SCHEMA = Path(__file__).parent / "schema_unified.sql"
ALL = ["granos", "cemento", "automotriz"]


def apply_schema(conn, name: str) -> bool:
    path = DATASETS_DIR / name / "schema.sql"
    if not path.is_file():
        print(f"  {name}  -> sin schema.sql en {path}")
        return False
    sql = path.read_text(encoding="utf-8")
    with conn.cursor() as cur:
        cur.execute(sql)
    conn.commit()
    print(f"  {name}  -> schema aplicado")
    return True


def apply_unified(conn) -> bool:
    """Vistas unificadas (series_actual / series_desest). Dependen de los 3 datasets."""
    if not UNIFIED_SCHEMA.is_file():
        return False
    sql = UNIFIED_SCHEMA.read_text(encoding="utf-8")
    with conn.cursor() as cur:
        cur.execute(sql)
    conn.commit()
    print("  unificadas  -> series_actual / series_desest")
    return True


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="etl init-db",
                                 description="Aplica los schema.sql a DATABASE_URL.")
    ap.add_argument("datasets", nargs="*", metavar="dataset",
                    help=f"datasets a inicializar (default: todos). Opciones: {', '.join(ALL)}")
    args = ap.parse_args(argv)
    names = args.datasets or ALL
    unknown = [n for n in names if n not in ALL]
    if unknown:
        ap.error(f"dataset(s) desconocido(s): {', '.join(unknown)}")

    print("[init-db]")
    conn = db.get_conn()
    aplicados = 0
    try:
        for name in names:
            aplicados += apply_schema(conn, name)
        # Las vistas unificadas referencian las 3 tablas: solo cuando se inicializan todas.
        if set(names) >= set(ALL):
            apply_unified(conn)
    finally:
        conn.close()
    print(f"resumen [init-db]  aplicados={aplicados}")


if __name__ == "__main__":
    main()
