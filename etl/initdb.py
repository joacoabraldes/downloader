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
DAILY_SCHEMA = Path(__file__).parent / "schema_daily.sql"
CONTROL_SCHEMA = Path(__file__).parent / "schema_control.sql"
# Datasets mensuales: gatean las vistas unificadas series_actual / series_desest.
MONTHLY = ["granos", "cemento", "automotriz", "patentamientos", "acero", "aves", "leche",
           "bovinos", "demanda_energia", "icc", "icg", "datos_gob", "comex"]
# Datasets diarios: gatean la vista unificada series_diarias_actual (carril separado).
DAILY = ["reservas_pasivos"]
# Datasets semanales: sin vista unificada propia (por ahora es uno solo y su grano no es
# comparable con el de las otras series: mezcla flujo semanal con acumulados de campaña).
WEEKLY = ["compras_granos"]
ALL = MONTHLY + DAILY + WEEKLY


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


def apply_sql_file(conn, path: Path, label: str) -> bool:
    if not path.is_file():
        return False
    sql = path.read_text(encoding="utf-8")
    with conn.cursor() as cur:
        cur.execute(sql)
    conn.commit()
    print(f"  {label}")
    return True


def apply_unified(conn) -> bool:
    """Vistas unificadas mensuales (series_actual / series_desest). Dependen de los 9 datasets."""
    return apply_sql_file(conn, UNIFIED_SCHEMA, "unificadas  -> series_actual / series_desest")


def apply_daily_unified(conn) -> bool:
    """Vista unificada diaria (series_diarias_actual). Depende de los datasets diarios."""
    return apply_sql_file(conn, DAILY_SCHEMA, "unificadas  -> series_diarias_actual")


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
        # Control de ejecución: no depende de ningún dataset, se aplica siempre.
        apply_sql_file(conn, CONTROL_SCHEMA, "control     -> etl_control_ejecucion / etl_control_ultima")
        for name in names:
            aplicados += apply_schema(conn, name)
        # Cada carril tiene su unificada: se aplican sólo cuando se inicializa el carril completo
        # (las vistas hacen UNION de las *_actual de ese carril y las referencian a todas).
        if set(names) >= set(MONTHLY):
            apply_unified(conn)
        if set(names) >= set(DAILY):
            apply_daily_unified(conn)
    finally:
        conn.close()
    print(f"resumen [init-db]  aplicados={aplicados}")


if __name__ == "__main__":
    main()
