"""Fuente de bcba_pas: tablero público de Power BI del Panorama Agrícola Semanal (BCBA).

El sitio de la BCBA está detrás de Cloudflare; el tablero "Publicar en la web" es la vía anónima y
estable (cliente en etl.core.powerbi). Una corrida hace, como mucho, 3 requests:

  1. GET de la página del visor -> cluster de la API.
  2. GET modelsAndExploration -> modelId / LastRefreshTime (si ya está procesado, termina acá).
  3. POST querydata con 5 consultas juntas: la medida Datos_al, las 3 dimensiones y la tabla de
     hechos `Histórico_PAS` (~1.700 filas, entra en una sola ventana).

Todo lo que no coincide con lo esperado (columna renombrada, cultivo o zona nueva, id de campaña
que no respeta la regla, fila duplicada, porcentaje fuera de rango) levanta `FormatoInesperado`:
la corrida falla SIN cargar nada.
"""
from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass

from etl.core import powerbi
from . import config


class FormatoInesperado(RuntimeError):
    pass


@dataclass
class Release:
    last_refresh: dt.datetime
    nombre_modelo: str


@dataclass
class Datos:
    fecha_datos: dt.date
    filas: list[dict]       # long: cultivo, campania, zona_id, zona, variable, valor
    celdas: int             # filas de la tabla de hechos (cultivo x campaña x zona)


def _parse_refresh(s: str) -> dt.datetime:
    # '2026-09-24T17:51:07.7' (UTC, sin zona). Se recorta a microsegundos.
    base, _, frac = s.partition(".")
    return dt.datetime.fromisoformat(base).replace(
        microsecond=int((frac + "000000")[:6]) if frac else 0, tzinfo=dt.timezone.utc)


def _parse_datos_al(s: str | None) -> dt.date:
    m = re.search(r"(\d{1,2})/(\d{1,2})/(\d{2,4})", s or "")
    if not m:
        raise FormatoInesperado(f"Datos_al sin fecha: {s!r}")
    d, mes, a = (int(x) for x in m.groups())
    return dt.date(a + 2000 if a < 100 else a, mes, d)


def _num(v, col: str):
    if v is None:
        return None
    try:
        return float(v)  # los decimales vienen como string en el DSR
    except (TypeError, ValueError) as e:
        raise FormatoInesperado(f"{col}: valor no numérico {v!r}") from e


def campania(id_campania: int, descripcion: str) -> str:
    """'2025/2026' -> '2025/26', validando la regla id = año inicial - 2009."""
    m = re.fullmatch(r"\s*(\d{4})\s*/\s*(\d{4})\s*", descripcion or "")
    if not m or int(m.group(2)) != int(m.group(1)) + 1:
        raise FormatoInesperado(f"campaña {id_campania}: descripción inesperada {descripcion!r}")
    if int(m.group(1)) - config.CAMPANIA_ID_BASE != id_campania:
        raise FormatoInesperado(f"campaña {id_campania} = {descripcion!r}: no respeta "
                                f"id = año - {config.CAMPANIA_ID_BASE}")
    return f"{m.group(1)}/{m.group(2)[-2:]}"


class Fuente:
    def __init__(self):
        self.cli = powerbi.Cliente(config.R_PARAM, config.KEY)

    @property
    def requests(self) -> int:
        return self.cli.requests

    def release(self) -> Release:
        """Requests 1 y 2. Levanta HTTPError (401/404 = clave caída) o PowerBIError."""
        self.cli.resolver_cluster()
        self.cli.modelo()
        return Release(_parse_refresh(self.cli.last_refresh), self.cli.nombre_modelo or "")

    def datos(self) -> Datos:
        """Request 3: todo en un POST. Valida y devuelve las filas en formato long."""
        col = powerbi.columna
        fact_cols = ["Id_Cultivo", "Id_Campaña", "Id_Zona"] + list(config.VARIABLES)
        med, cult, camp, zon, fact = self.cli.query([
            ("medidas_Proyecto", [powerbi.medida("t", "Datos_al")]),
            ("Cultivos", [col("t", "ID_Cultivos"), col("t", "Cultivo")]),
            ("Campañas", [col("t", "id_campaña"), col("t", "descripcion")]),
            ("Zonas", [col("t", "ID_Zona"), col("t", "Zona"), col("t", "Descripción")]),
            (config.FACT, [col("t", c) for c in fact_cols]),
        ])
        if len(med) != 1:
            raise FormatoInesperado(f"Datos_al: {len(med)} filas")
        fecha = _parse_datos_al(med[0]["Datos_al"])

        # Dimensiones: verificar el mapeo por id contra config (nombre incluido).
        cultivos = {r["ID_Cultivos"]: r["Cultivo"] for r in cult}
        if cultivos != {i: n for i, (n, _) in config.CULTIVOS.items()}:
            raise FormatoInesperado(f"dimensión Cultivos distinta de la esperada: {cultivos}")
        zonas = {r["ID_Zona"]: (r["Zona"], r["Descripción"]) for r in zon}
        if zonas != config.ZONAS:
            raise FormatoInesperado(f"dimensión Zonas distinta de la esperada: {zonas}")
        campanias = {r["id_campaña"]: campania(r["id_campaña"], r["descripcion"]) for r in camp}

        n = len(fact)
        if not config.MIN_FILAS <= n <= config.MAX_FILAS:
            raise FormatoInesperado(f"{config.FACT}: {n} filas, fuera de "
                                    f"[{config.MIN_FILAS}, {config.MAX_FILAS}]")
        filas: list[dict] = []
        vistas: set[tuple] = set()
        for r in fact:
            ic, ica, iz = r["Id_Cultivo"], r["Id_Campaña"], r["Id_Zona"]
            if ic not in config.CULTIVOS:
                raise FormatoInesperado(f"Id_Cultivo desconocido: {ic}")
            if ica not in campanias:
                raise FormatoInesperado(f"Id_Campaña sin fila en Campañas: {ica}")
            if iz not in config.ZONAS:
                raise FormatoInesperado(f"Id_Zona desconocido: {iz}")
            if (ic, ica, iz) in vistas:
                raise FormatoInesperado(f"fila duplicada en {config.FACT}: {(ic, ica, iz)}")
            vistas.add((ic, ica, iz))
            for fuente_col, variable in config.VARIABLES.items():
                v = _num(r[fuente_col], fuente_col)
                if v is None:
                    continue  # celda vacía = no hay fila (no es 0)
                if v < 0 or (variable.endswith("_pct") and v > 100 + 1e-6):
                    raise FormatoInesperado(f"{variable} fuera de rango: {v} en "
                                            f"{(ic, ica, iz)}")
                filas.append({
                    "cultivo": config.CULTIVOS[ic][1], "campania": campanias[ica],
                    "zona_id": iz, "zona": config.ZONA_CODIGO[iz], "variable": variable,
                    "valor": v,
                })
        return Datos(fecha, filas, n)
