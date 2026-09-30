"""Cliente anónimo mínimo para un reporte de Power BI "Publicar en la web" (publish-to-web).

Usa sólo los endpoints públicos que usa el visor del navegador, autenticados con el header
`X-PowerBI-ResourceKey` (la `k` del parámetro `r=` de la URL /view). Nada de tokens ni login.

Flujo (un request por paso, con pausa entre cada uno):

  1. GET de la página /view -> `resolvedClusterUri` ('...-redirect.analysis.windows.net'); la API
     vive en el mismo host con '-api' en vez de '-redirect'. Se resuelve EN CADA CORRIDA: Microsoft
     puede mover el reporte de cluster.
  2. GET {cluster}/public/reports/{key}/modelsAndExploration -> modelId, dbName (DatasetId),
     LastRefreshTime y el id del reporte. Tampoco se hardcodean: si el autor republica el .pbix
     cambian.
  3. POST {cluster}/public/reports/querydata con una o varias consultas semánticas (DSR).

Respuesta DSR (DataShapeResult), la parte que hay que decodificar a mano:
  - `PH[0].DM0` = filas. La primera trae `S` (esquema: una entrada por columna; `DN` = nombre
    del diccionario si la columna viene codificada por diccionario).
  - Cada fila: `C` = valores presentes, `R` = bitmask "igual que la fila anterior",
    `Ø` = bitmask "null". Un int en una columna con `DN` es un índice a `ValueDicts[DN]`.
  - `RT` presente = la ventana se cortó: pasarlo de vuelta como `RestartTokens`.
  - Los decimales pueden venir como STRING ('24.46...'): el que consume convierte.
  - Un error de consulta (columna/tabla renombrada o borrada) NO es un HTTP != 200: viene como
    `DataShapes[0]['odata.error']` con 200. `query` lo levanta como `PowerBIError`.
"""
from __future__ import annotations

import re
import time
import uuid

from etl.core import http

VIEW_URL = "https://app.powerbi.com/view?r={r}"
PAUSA = 1.5  # segundos entre requests: es un servicio de Microsoft, no un host .gob.ar


class PowerBIError(RuntimeError):
    """El reporte no responde como se espera (clave caída, modelo cambiado, consulta inválida)."""


def columna(entidad_alias: str, nombre: str) -> dict:
    return {"Column": {"Expression": {"SourceRef": {"Source": entidad_alias}},
                       "Property": nombre}, "Name": nombre}


def medida(entidad_alias: str, nombre: str) -> dict:
    return {"Measure": {"Expression": {"SourceRef": {"Source": entidad_alias}},
                        "Property": nombre}, "Name": nombre}


class Cliente:
    """Una sesión contra un reporte publicado. Cuenta requests y respeta la pausa entre ellos."""

    def __init__(self, r_param: str, key: str, *, pausa: float = PAUSA):
        self.r_param = r_param
        self.key = key
        self.pausa = pausa
        self.requests = 0
        self._ultimo = 0.0
        self.cluster: str | None = None
        self.model_id: int | None = None
        self.db_name: str | None = None
        self.report_id: str | None = None
        self.last_refresh: str | None = None
        self.nombre_modelo: str | None = None
        self._headers = {
            "X-PowerBI-ResourceKey": key,
            "User-Agent": "Mozilla/5.0",
            "ActivityId": str(uuid.uuid4()),
            "RequestId": str(uuid.uuid4()),
        }

    def _esperar(self) -> None:
        falta = self.pausa - (time.monotonic() - self._ultimo)
        if self._ultimo and falta > 0:
            time.sleep(falta)

    def _fetch(self, url: str, **kw):
        self._esperar()
        try:
            return http.fetch(url, reintentos=2, espera_base=5.0, **kw)
        finally:
            self._ultimo = time.monotonic()
            self.requests += 1

    def resolver_cluster(self) -> str:
        """Paso 1: cluster de la API desde el HTML del visor."""
        html = self._fetch(VIEW_URL.format(r=self.r_param), timeout=60).text
        m = re.search(r"resolvedClusterUri\s*=\s*'(https://[^']+)'", html)
        if not m:
            raise PowerBIError("la página del visor no trae resolvedClusterUri (¿reporte "
                               "despublicado o visor cambiado?)")
        self.cluster = m.group(1).rstrip("/").replace("-redirect.", "-api.")
        return self.cluster

    def modelo(self) -> dict:
        """Paso 2: modelId / dbName / LastRefreshTime / id del reporte."""
        if self.cluster is None:
            self.resolver_cluster()
        url = (f"{self.cluster}/public/reports/{self.key}/modelsAndExploration"
               f"?preferReadOnlySession=true")
        js = self._fetch(url, timeout=60, headers=self._headers).json()
        modelos = js.get("models") or []
        if len(modelos) != 1:
            raise PowerBIError(f"modelsAndExploration devolvió {len(modelos)} modelos (se "
                               f"esperaba 1)")
        m = modelos[0]
        rep = (js.get("exploration") or {}).get("report") or {}
        self.model_id = m["id"]
        self.db_name = m["dbName"]
        self.last_refresh = m.get("LastRefreshTime")
        self.nombre_modelo = m.get("displayName")
        self.report_id = rep.get("objectId")
        if not (self.model_id and self.db_name and self.last_refresh and self.report_id):
            raise PowerBIError("modelsAndExploration sin modelId/dbName/LastRefreshTime/reporte")
        return m

    def _comando(self, entidad: str, select: list[dict], top: int,
                 restart: list | None = None) -> dict:
        window = {"Count": top}
        if restart:
            window["RestartTokens"] = restart
        return {"SemanticQueryDataShapeCommand": {
            "Query": {"Version": 2, "From": [{"Name": "t", "Entity": entidad, "Type": 0}],
                      "Select": select},
            "Binding": {
                "Primary": {"Groupings": [{"Projections": list(range(len(select)))}]},
                "DataReduction": {"DataVolume": 4, "Primary": {"Window": window}},
                "Version": 1,
            },
            "ExecutionMetricsKind": 1,
        }}

    def query(self, consultas: list[tuple[str, list[dict]]], *, top: int = 30000
              ) -> list[list[dict]]:
        """Paso 3: varias consultas en UN request. `consultas` = [(entidad, select), ...] con
        select armado con `columna(...)`/`medida(...)` sobre el alias 't'. Devuelve una lista de
        filas (dicts por `Name`) por consulta, en el mismo orden.

        Si alguna ventana viene cortada (`RT`), se pide la continuación de ESA consulta sola.
        """
        if self.model_id is None:
            self.modelo()
        resultados: list[list[dict]] = [[] for _ in consultas]
        pendientes = {i: None for i in range(len(consultas))}
        while pendientes:
            idx = list(pendientes)
            body = {
                "version": "1.0.0",
                "queries": [{
                    "Query": {"Commands": [self._comando(consultas[i][0], consultas[i][1], top,
                                                         pendientes[i])]},
                    "QueryId": "",
                    "ApplicationContext": {"DatasetId": self.db_name,
                                           "Sources": [{"ReportId": self.report_id}]},
                } for i in idx],
                "cancelQueries": [],
                "modelId": self.model_id,
            }
            js = self._fetch(f"{self.cluster}/public/reports/querydata?synchronous=true",
                             json_body=body, timeout=120, headers=self._headers).json()
            res = js.get("results") or []
            if len(res) != len(idx):
                raise PowerBIError(f"querydata devolvió {len(res)} resultados para {len(idx)} "
                                   f"consultas")
            nuevos = {}
            for i, r in zip(idx, res):
                nombres = [s["Name"] for s in consultas[i][1]]
                filas, rt = decode_dsr(r, nombres, consultas[i][0])
                resultados[i] += filas
                if rt:
                    nuevos[i] = rt
            pendientes = nuevos
        return resultados


def decode_dsr(resultado: dict, nombres: list[str], entidad: str = "") -> tuple[list[dict], list | None]:
    """Decodifica un resultado DSR a filas {nombre: valor}. Devuelve (filas, restart_tokens)."""
    try:
        dsr = resultado["result"]["data"]["dsr"]
    except (KeyError, TypeError) as e:
        raise PowerBIError(f"{entidad}: respuesta sin dsr ({str(resultado)[:300]})") from e
    for shape in dsr.get("DataShapes") or []:
        err = shape.get("odata.error")
        if err:
            msg = (err.get("message") or {}).get("value") or err.get("code")
            raise PowerBIError(f"{entidad}: {msg}")
    ds = dsr["DS"][0]
    dicts = ds.get("ValueDicts", {})
    ph = ds.get("PH") or [{}]
    filas_raw = next((v for k, v in ph[0].items() if k.startswith("DM")), [])
    if not filas_raw:
        return [], None
    esquema = filas_raw[0]["S"]
    dn = [s.get("DN") for s in esquema]
    n = len(esquema)
    if n != len(nombres):
        raise PowerBIError(f"{entidad}: esquema DSR con {n} columnas, se pidieron {len(nombres)}")
    out, prev = [], [None] * n
    for fila in filas_raw:
        if "C" not in fila and any(s.get("N") in fila for s in esquema):
            # Fila de sólo medidas (sin agrupación): los valores vienen con su nombre ('M0').
            cur = [fila.get(s.get("N")) for s in esquema]
            out.append(dict(zip(nombres, cur)))
            prev = cur
            continue
        repetir, nulos = fila.get("R", 0), fila.get("Ø", 0)
        vals = iter(fila.get("C", []))
        cur = []
        for i in range(n):
            if repetir & (1 << i):
                cur.append(prev[i])
            elif nulos & (1 << i):
                cur.append(None)
            else:
                v = next(vals)
                if dn[i] is not None and isinstance(v, int):
                    v = dicts[dn[i]][v]
                cur.append(v)
        out.append(dict(zip(nombres, cur)))
        prev = cur
    return out, ds.get("RT")
