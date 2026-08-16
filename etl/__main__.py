"""CLI del monorepo de ETLs.

  python -m etl <dataset> [run|load-history] [flags]   # dataset: granos|cemento|automotriz|patentamientos|acero|aves|leche|bovinos|demanda_energia|icc|icg|datos_gob|comex|reservas_pasivos|compras_granos
  python -m etl init-db  [datasets...]                  # aplica los schema.sql
  python -m etl redesest [datasets...] [--clean]        # recalcula la desest desde la base

Ejemplos:
  python -m etl init-db
  python -m etl granos load-history
  python -m etl granos --months-back 12
  python -m etl cemento --month 2026-04
  python -m etl automotriz load-history
  python -m etl redesest                     # recalcula la desest de los 3 (sin bajar de la web)
  python -m etl redesest --clean             # borra las desest y las regenera (rebuild limpio)
"""
from __future__ import annotations

import datetime as dt
import importlib
import sys

from etl.core import control, report

DATASETS = ["granos", "cemento", "automotriz", "patentamientos", "acero", "aves", "leche",
            "bovinos", "demanda_energia", "icc", "icg", "datos_gob", "comex",
            "reservas_pasivos", "compras_granos"]
SUBCOMMANDS = {"run", "load-history"}
USAGE = (
    "uso: python -m etl <dataset> [run|load-history] [flags]\n"
    "     python -m etl init-db  [datasets...]\n"
    "     python -m etl export   [datasets...] [--dir CARPETA]\n"
    "     python -m etl redesest [datasets...] [--clean] [--x13-out DIR]\n"
    f"     datasets: {', '.join(DATASETS)}"
)


def _correr(modulo: str, rest, *, dataset: str, comando: str) -> None:
    """Corre un comando dejando SIEMPRE su huella en etl_control_ejecucion.

    El `finally` cubre los tres finales posibles: corrida normal, corrida con fallas
    registradas, y excepción no capturada. Si no se registrara en el último caso, un ETL que
    revienta seria justo el que no deja rastro, que es el que mas importa ver.
    """
    inicio = dt.datetime.now(dt.timezone.utc)
    try:
        importlib.import_module(modulo).main(rest)
    except BaseException as e:  # incluye SystemExit y KeyboardInterrupt
        report.record_failure(f"{dataset} / {comando}: excepción no capturada: {e!r}")
        raise
    finally:
        control.registrar(dataset=dataset, comando=comando, inicio=inicio)


def _exit_on_failures() -> None:
    """Sale con código 1 si la corrida registró fallas (ver `etl.core.report.record_failure`).

    El resumen va por stderr: en el cron, `scripts/run_etl.sh` lo reenvía para que dispare el
    mail del MAILTO. Sin esto la corrida terminaba en 0 aunque no hubiera traído un solo dato.
    """
    fails = report.failures()
    if not fails:
        return
    print(f"FALLO: {len(fails)} problema(s) en la corrida:", file=sys.stderr)
    for f in fails:
        print(f"  - {f}", file=sys.stderr)
    sys.exit(1)


def main(argv=None) -> None:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help"):
        print(USAGE)
        sys.exit(0 if argv else 2)

    cmd, rest = argv[0], argv[1:]

    if cmd == "init-db":
        importlib.import_module("etl.initdb").main(rest)
        return

    if cmd == "export":
        importlib.import_module("etl.export").main(rest)
        return

    if cmd == "redesest":
        _correr("etl.redesest", rest, dataset="redesest", comando="redesest")
        _exit_on_failures()
        return

    if cmd not in DATASETS:
        print(f"dataset desconocido: {cmd!r}\n{USAGE}", file=sys.stderr)
        sys.exit(2)

    sub = "run"
    if rest and rest[0] in SUBCOMMANDS:
        sub, rest = rest[0], rest[1:]
    module = "load_history" if sub == "load-history" else "run"
    _correr(f"etl.datasets.{cmd}.{module}", rest, dataset=cmd, comando=sub)
    _exit_on_failures()


if __name__ == "__main__":
    main()
