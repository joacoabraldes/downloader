"""Huella de cada corrida en `etl_control_ejecucion` (ver `etl/schema_control.sql`).

Una fila por ejecución, ande o no. Existe porque las tablas de datos son append-only con
`insert_if_changed`: una corrida sin cambios no escribe nada, así que `max(ingested_at)` dice
"último día que un valor cambió", no "último día que el ETL corrió". Con esto la app que
consume los datos puede preguntar por el PROCESO y no sólo por el dato:

    select * from etl_control_ultima where estado = 'falla' or horas_desde > 26;

REGLA: registrar nunca puede romper ni cambiar el resultado de la corrida. Si esta escritura
falla (base caída, tabla sin crear), se avisa por stderr y se sigue: el ETL ya hizo su trabajo
y el exit code lo decide `report.failures()`, no esto.
"""
from __future__ import annotations

import datetime as dt
import importlib
import socket
import sys

from etl.core import db, report


def _ultimo_dato(conn, dataset: str):
    """max(date) observado del dataset (excluye la desest), o None si no aplica."""
    try:
        config = importlib.import_module(f"etl.datasets.{dataset}.config")
    except ModuleNotFoundError:
        return None  # 'redesest' y demás comandos sin tabla propia
    tabla = getattr(config, "TABLE", None)
    if not tabla:
        return None
    with conn.cursor() as cur:
        cur.execute(f"select max(date) from {tabla} "
                    f"where estado is distinct from 'desestacionalizado'")
        r = cur.fetchone()
    return r[0] if r else None


def registrar(*, dataset: str, comando: str, inicio: dt.datetime) -> None:
    """Inserta la fila de control de esta corrida. No propaga errores."""
    fallas = report.failures()
    c = report.counts()
    d = report.desest_counts()
    fin = dt.datetime.now(dt.timezone.utc)
    try:
        conn = db.get_conn()
    except Exception as e:
        print(f"[control] no se pudo registrar la corrida (sin conexión): {e}", file=sys.stderr)
        return
    try:
        ultimo = _ultimo_dato(conn, dataset)
        with conn.cursor() as cur:
            cur.execute(
                """insert into etl_control_ejecucion
                   (dataset, comando, inicio, fin, duracion_seg, estado, fallas,
                    leidos, nuevos, actualizados, sin_cambios, saltados, no_publicado,
                    desest_series, desest_upserts, desest_saltadas, ultimo_dato, host)
                   values (%s,%s,%s,%s,%s,%s,%s, %s,%s,%s,%s,%s,%s, %s,%s,%s, %s,%s)""",
                (dataset, comando, inicio, fin, round((fin - inicio).total_seconds(), 3),
                 "falla" if fallas else "ok", fallas or None,
                 c.get("leidos"), c.get("nuevos"), c.get("actualizados"),
                 c.get("sin_cambios"), c.get("saltados"), c.get("no_publicado"),
                 d.get("series"), d.get("upserts"), d.get("saltadas"),
                 ultimo, socket.gethostname()),
            )
        conn.commit()
    except Exception as e:
        # Puede pasar legítimamente la primera vez: falta `python -m etl init-db`.
        print(f"[control] no se pudo registrar la corrida: {e}", file=sys.stderr)
    finally:
        conn.close()
