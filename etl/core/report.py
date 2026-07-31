"""Reporte uniforme de los comandos (mismo output para todos).

Cada comando imprime:
  - un header `[<dataset> / <accion>]`
  - opcionalmente líneas por ítem con su status explícito
  - una línea final `resumen [<dataset> / <accion>]  leidos=.. nuevos=.. actualizados=.. ...`

Los status vienen de `db.insert_if_changed`: nuevo / actualizado / sin_cambios / saltado.
`no_publicado` lo agrega el run cuando la fuente no tiene el mes. Solo ASCII (corre igual
en Linux y en la consola de Windows).

Además del reporte legible, el módulo lleva un registro de **fallas** de la corrida (ver
`record_failure`): fuente caída, parseo roto, serie que X-13 no pudo ajustar. `python -m etl`
lo consulta al terminar y sale con código != 0 si quedó alguna. Una corrida que no pudo traer
el dato NO es exitosa, y el cron necesita poder distinguirlo: hasta jul-2026 todos estos casos
salían con código 0 y la falla pasaba inadvertida (ver el wrapper `scripts/run_etl.sh`).
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

# Fallas acumuladas de la corrida (proceso-global: un `python -m etl <ds>` corre un solo ETL).
_FAILURES: list[str] = []


def record_failure(detail: str) -> None:
    """Registra una falla de la corrida. Hace que el proceso salga con código != 0."""
    _FAILURES.append(detail)


def failures() -> list[str]:
    """Fallas acumuladas hasta ahora (lista de descripciones legibles)."""
    return list(_FAILURES)


def reset_failures() -> None:
    """Limpia el registro (para tests o para correr varios ETLs en un mismo proceso)."""
    _FAILURES.clear()


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

    @property
    def changed(self) -> int:
        """Registros nuevos o actualizados en esta corrida (0 = nada para desestacionalizar)."""
        return self.counts["nuevos"] + self.counts["actualizados"]

    def info(self, text: str) -> None:
        """Línea informativa secundaria (rango leído, fuente, etc.)."""
        print(f"  {text}")

    def error(self, text: str) -> None:
        """Falla de la corrida: la imprime y marca el proceso para salir con código != 0.

        Para lo que impide traer el dato (fuente caída, HTML/PDF que no parsea). No usar para
        un mes que la fuente todavía no publicó: eso es `note(..., "no publicado")`.
        """
        record_failure(f"{self.title}: {text}")
        print(f"  ERROR {text}")

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

    def note(self, period, text: str, status: str = "no_publicado", *,
             failure: bool = False) -> None:
        """Línea por mes sin inserción: `  2026-06  -> no publicado`.

        `failure=True` cuando el mes se saltea porque algo se rompió (no porque la fuente aún
        no lo publicó): además de contarlo, registra la falla para el código de salida.
        """
        self.tally(status)
        if failure:
            record_failure(f"{self.title} {_period(period)}: {text}")
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
        """result = dict de `seasonal.deseasonalize` (tag, status, n, mode, reason, outdir).

        `status="error"` (X-13 reventó o no dejó d11) cuenta como falla de la corrida. Un
        `status="skipped"` NO: es un salteo por diseño (serie corta, huecos en los meses).
        """
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
            if result["status"] == "error":
                record_failure(f"{self.title} {tag}: {result['reason']}")
            print(f"  {tag:14} -> {result['status']}: {result['reason']}")

    def summary(self) -> None:
        body = f"series={self.series}  upserts={self.upserts}"
        if self.saltadas:
            body += f"  saltadas={self.saltadas}"
        print(f"resumen [{self.title}]  {body}")
