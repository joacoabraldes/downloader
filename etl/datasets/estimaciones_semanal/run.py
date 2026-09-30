"""ETL del Informe Semanal de Estimaciones Agrícolas (MAGyP). Backfill e incremental son lo mismo.

Corrida normal (cron): 1 GET del índice del mes (+ el del mes anterior los primeros 7 días) y
sólo los PDFs semanales que no estén en `etl_estimaciones_semanal_informes`. Un PDF que no parsea se
reporta como falla y NO se registra (la próxima corrida lo reintenta).

Backfill en tandas (host MAGyP compartido, ver etl.core.magyp_informes):

    python -m etl estimaciones_semanal --desde 2025-05 --max-requests 30 \\
        --cache /home/jmt/data/etls/cache/estimaciones_informes

  repetir hasta que diga "sin informes nuevos". Flags: ver `--help`.
"""
from __future__ import annotations

from etl.core import magyp_informes
from . import config, source


def main(argv=None) -> None:
    magyp_informes.correr(
        dataset="estimaciones_semanal", tipo=config.TIPO, tabla=config.TABLE,
        tabla_informes=config.INFORMES_TABLE, key_cols=config.KEY_COLS,
        extra_cols=config.EXTRA_COLS, parse=source.parse_pdf, argv=argv,
        descripcion=__doc__.splitlines()[0],
    )


if __name__ == "__main__":
    main()
