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
CONTROL_SALUD_SCHEMA = Path(__file__).parent / "schema_control_salud.sql"
ESTIMACIONES_SCHEMA = Path(__file__).parent / "schema_estimaciones.sql"
# Datasets mensuales: gatean las vistas unificadas series_actual / series_desest.
MONTHLY = ["granos", "cemento", "automotriz", "patentamientos", "transferencias", "acero",
           "aves", "leche", "bovinos", "demanda_energia", "hidrocarburos",
           "ventas_combustibles", "refinacion", "escrituras_caba", "icc", "icg", "datos_gob",
           "comex"]
# Datasets diarios: gatean la vista unificada series_diarias_actual (carril separado).
DAILY = ["reservas_pasivos", "fob_granos"]
# Datasets semanales: sin vista unificada de carril (los de estimaciones entran a
# `estimaciones_actual`, ver apply_estimaciones). Su grano no es comparable con el de las
# otras series (compras_granos mezcla flujo semanal con acumulados de campaña; cot son posiciones
# abiertas en contratos, un stock de otro mercado; estimaciones_semanal es avance de labores por
# zona, sin `date`: su grano es la fecha de corte de cada semana).
# bcba_pas: Panorama Agrícola Semanal de la BCBA, grano (cultivo, campania, zona, variable) con
# vintages semanales.
WEEKLY = ["compras_granos", "cot", "estimaciones_semanal", "bcba_pas"]
# Datasets anuales (campaña agrícola): sin vista de carril (sí en estimaciones_actual).
# estimaciones_agricolas no tiene `date`: su grano es (cultivo, campania, departamento_id).
# estimaciones_mensual: grano (cultivo, campania, variable), la estimación vigente de la campaña.
ANUAL = ["estimaciones_agricolas", "estimaciones_mensual"]
ALL = MONTHLY + DAILY + WEEKLY + ANUAL


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


def apply_control_salud(conn) -> bool:
    """`etl_control_salud`. Va DESPUÉS de los datasets: lee `etl_datos_gob_salud`.

    Es LA vista de alertas, así que tiene que existir después de cualquier init-db, incluido uno
    parcial que no nombre a datos_gob: si falta su vista por serie, se aplica ese schema primero.

    Además se sincroniza la dimensión de datos_gob: el umbral `dias_max_dato` sólo lo escribe
    `sincronizar_dimension`, y sin eso un init-db sobre una base existente deja las 14 series en
    `SIN_UMBRAL` —falsa alarma de DATO_VIEJO— hasta la próxima corrida del ETL.
    """
    from etl.datasets.datos_gob.run import sincronizar_dimension

    with conn.cursor() as cur:
        cur.execute("select to_regclass('etl_datos_gob_salud') is not null")
        existe = cur.fetchone()[0]
    if not existe:
        apply_schema(conn, "datos_gob")
    sincronizar_dimension(conn)
    return apply_sql_file(conn, CONTROL_SALUD_SCHEMA, "control     -> etl_control_salud")


def apply_estimaciones(conn) -> bool:
    """`estimaciones_actual`: une las _actual de las 4 fuentes de estimaciones de cultivos.

    Se aplica siempre, al final: el bloque SQL arma la UNION sólo con las vistas que existen
    (tolera una base sin alguno de los datasets) y la recrea con DROP + CREATE.
    """
    return apply_sql_file(conn, ESTIMACIONES_SCHEMA, "unificadas  -> estimaciones_actual")


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
        # estimaciones_actual depende de las _actual de 4 datasets: se saca antes de aplicar sus
        # schemas (un cambio de columnas en una _actual no puede quedar trabado por ella) y se
        # recrea al final.
        with conn.cursor() as cur:
            cur.execute("drop view if exists estimaciones_actual")
        conn.commit()
        for name in names:
            aplicados += apply_schema(conn, name)
        # Cada carril tiene su unificada: se aplican sólo cuando se inicializa el carril completo
        # (las vistas hacen UNION de las *_actual de ese carril y las referencian a todas).
        if set(names) >= set(MONTHLY):
            apply_unified(conn)
        if set(names) >= set(DAILY):
            apply_daily_unified(conn)
        apply_estimaciones(conn)
        # Al final: depende de la vista por serie de datos_gob (ver apply_control_salud).
        apply_control_salud(conn)
    finally:
        conn.close()
    print(f"resumen [init-db]  aplicados={aplicados}")


if __name__ == "__main__":
    main()
