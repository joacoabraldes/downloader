#!/usr/bin/env bash
# Wrapper de cron para los ETLs.
#
# Corre `python -m etl <dataset>`, manda TODO (stdout + stderr) al log del dataset y, si la
# corrida falla, ADEMAS repite el final por stderr.
#
# Ese reenvio es el punto del script: cron solo mailea al MAILTO cuando el job produce output,
# no cuando sale con codigo != 0. Con el `>> log 2>&1` que usaban las lineas del crontab, todo
# el output se iba al archivo y ninguna falla llegaba al mail -- ni siquiera con el exit code
# arreglado. Acá el log se sigue escribiendo igual y stderr queda libre para disparar el aviso.
#
#   uso:  scripts/run_etl.sh <dataset> [args...]
#   cron: 0 11 20-31 * * /home/jmt/dev/downloader/scripts/run_etl.sh aves

set -uo pipefail

REPO=/home/jmt/dev/downloader
LOGDIR=/home/jmt/data/etls
COLA=25   # lineas finales del log que se mandan por mail cuando falla

ds=${1:?uso: run_etl.sh <dataset> [args...]}
shift

cd "$REPO" || { echo "run_etl.sh: no se pudo entrar a $REPO" >&2; exit 2; }
mkdir -p "$LOGDIR" || { echo "run_etl.sh: no se pudo crear $LOGDIR" >&2; exit 2; }
log="$LOGDIR/$ds.log"

# Se junta la salida en vez de streamearla para poder mandar el tail por mail. Las corridas son
# cortas (la mas lenta ronda el minuto), asi que no hace falta ver el avance en vivo.
out=$(.venv/bin/python -m etl "$ds" "$@" 2>&1)
rc=$?

printf '%s\n' "$out" >> "$log"

if [ "$rc" -ne 0 ]; then
    printf 'ETL %s FALLO (exit %d) -- %s\nlog: %s\n\n%s\n' \
        "$ds" "$rc" "$(date '+%F %T %Z')" "$log" \
        "$(printf '%s\n' "$out" | tail -n "$COLA")" >&2
fi

exit "$rc"
