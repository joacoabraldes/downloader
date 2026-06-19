"""Reporte uniforme de los comandos (mismo output para todos).

Cada comando imprime:
  - un header `[<dataset> / <accion>]`
  - opcionalmente líneas por ítem con su status explícito
  - una línea final `resumen [<dataset> / <accion>]  leidos=.. nuevos=.. actualizados=.. ...`

Los status vienen de `db.insert_if_changed`: nuevo / actualizado / sin_cambios / saltado.
`no_publicado` lo agrega el run cuando la fuente no tiene el mes. Solo ASCII (corre igual
en Linux y en la consola de Windows).
"""
from __future__ import annotations

import datetime as dt

_STATUS_KEY = {
    "nuevo": "nuevos",
    "actualizado": "actualizados",
    "sin_cambios": "sin_cambios",
    "saltado": "saltados",
    "no_publicado": "no_publicado",
}
# Claves que siempre se muestran; saltados/no_publicado solo si > 0.
_ALWAYS = ["leidos", "nuevos", "actualizados", "sin_cambios"]
_OPTIONAL = ["saltados", "no_publicado"]


def _fmt(v) -> str:
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v)


def _period(p) -> str:
    return p if isinstance(p, str) else f"{p:%Y-%m}"


class Report:
    """Acumula status y los imprime de forma uniforme."""

    def __init__(self, dataset: str, accion: str):
        self.title = f"{dataset} / {accion}"
        self.counts = {k: 0 for k in _ALWAYS + _OPTIONAL}
        print(f"[{self.title}]")

    def info(self, text: str) -> None:
        """Línea informativa secundaria (rango leído, fuente, etc.)."""
        print(f"  {text}")

    def tally(self, status: str) -> str:
        """Cuenta un status sin imprimir línea (para cargas masivas)."""
        if status != "no_publicado":
            self.counts["leidos"] += 1
        self.counts[_STATUS_KEY[status]] += 1
        return status

    def item(self, period, status: str, **fields) -> str:
        """Cuenta + imprime una línea por ítem: `  2026-05  valor=37762  -> nuevo`."""
        self.tally(status)
        body = " ".join(f"{k}={_fmt(v)}" for k, v in fields.items() if v is not None)
        parts = [_period(period)] + ([body] if body else []) + [f"-> {status}"]
        print("  " + "  ".join(parts))
        return status

    def note(self, period, text: str, status: str = "no_publicado") -> None:
        """Línea por mes sin inserción: `  2026-06  -> no publicado`."""
        self.tally(status)
        print(f"  {_period(period)}  -> {text}")

    def summary(self) -> None:
        keys = list(_ALWAYS) + [k for k in _OPTIONAL if self.counts[k]]
        body = "  ".join(f"{k}={self.counts[k]}" for k in keys)
        print(f"resumen [{self.title}]  {body}")


class DesestReport:
    """Bloque uniforme de la etapa X-13: `[dataset / desest]` + una línea por serie."""

    def __init__(self, dataset: str):
        self.title = f"{dataset} / desest"
        self.series = self.upserts = self.saltadas = 0
        print(f"[{self.title}]")

    def add(self, result: dict) -> None:
        """result = dict de `seasonal.deseasonalize` (tag, status, n, mode, reason, outdir)."""
        self.series += 1
        tag = result["tag"]
        if result["status"] == "ok":
            self.upserts += result["n"]
            line = f"  {tag:14} upserts={result['n']}  modo={result['mode']}"
            if result.get("outdir"):
                line += f"  (salida: {result['outdir']})"
            print(line)
        else:
            self.saltadas += 1
            print(f"  {tag:14} -> {result['status']}: {result['reason']}")

    def summary(self) -> None:
        body = f"series={self.series}  upserts={self.upserts}"
        if self.saltadas:
            body += f"  saltadas={self.saltadas}"
        print(f"resumen [{self.title}]  {body}")
