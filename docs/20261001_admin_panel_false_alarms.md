# Fix false alarms in the ETL health panel (crud_bonds: server/routes/admin.js)

Handoff for the crud_bonds coding agent. The admin health panel flags ETLs that are actually
fine on predictable days of the month. Diagnosed on 2026-10-01 against logs, DB and source
sites. Line numbers are approximate — verify before editing.

## 1. reca_fiscal heartbeat: FALLA at every window start before 20:00 (~admin.js:254, :275)

Current rule: `inWindow = day 1..10` and `heartbeatRed = inWindow && horas_desde > 36`,
with `horas_desde` from `max(started_at)` in `reca_ingest_log WHERE source IS NULL`.

The cron is now (changed 2026-10-01):

```
0 20 1-10  * * /home/jmt/dev/reca/reca-cron.sh   # recaudación (published days 1-6 of M+1)
0 20 16-18 * * /home/jmt/dev/reca/reca-cron.sh   # base caja IMIG/AIF (published ~16-18 of M+1)
```

Bug: on day 1 (and now on day 16) the window is "open" but the run only fires at 20:00, so
from 00:00 to 20:00 the last run is ~20 days old → FALLA with "sin corrida hace 491h".

Fix: compute the most recent *scheduled* slot (20:00 on a day in 1-10 or 16-18, at or before
now, Argentina time) and mark red only if there is no heartbeat at/after that slot plus a
grace period (e.g. 2 h). Outside both windows the heartbeat check must not fire. Keep the
schedule as a single constant next to a comment pointing at the crontab, and update the
comment at ~:226-227.

## 2. reca_fiscal staleness (IMIG/AIF "atrasado 3m") (~admin.js:256-261, :278)

No code change needed once #1 is fixed: with the new 16-18 run, base caja for month M is
loaded around day 18 of M+1, so `monthsBehind` for imig/aif is normally 1-2 and the `>= 3`
threshold is right. Just make sure the comment describing the expected lag says
"M is loaded ~day 18 of M+1" (it used to say M+2).

## 3. rem: off-by-one, ATRASADO on days 1-~7 every month (~admin.js:303-319, rule at ~:312, :315)

Current rule: months between `max(fecha_pronostico)` and the current month, ATRASADO if `>= 2`.
REM for month M is published by BCRA in the first days of M+1; the cron is `30 7 3-10 * *`.
So on days 1-10 of M+1 the latest survey is legitimately M-1 → mb = 2 → false ATRASADO.

Fix: ATRASADO if `mb >= 3`, or `mb >= 2 && day_of_month > 10` (after the cron window closes).
`ultima_corrida: null` (~:316) is by design — rem_downloader writes no run record. Leave it,
but consider showing "— (sin registro de corridas)" so it doesn't look like a bug.

## 4. balance_cambiario / mercado_cambios: "last run" is really "last write" (~admin.js:185-211, :192, :203)

The column shows `max(updated_at)`, which hides runs that execute and fail. Between
2026-09-25 and 2026-09-30 the job ran and failed daily, but the panel kept showing 03/09.
Minimal fix: rename the label to "último dato escrito". The staleness rule (ATRASADO when
`max(fecha) < date_trunc('month', now) - 2 months`) is correct — that one was a real failure,
now fixed in etl_bce_cambiario.

## Related: mercado_cambios sums were inflated (no code change)

Until 2026-10-01 `etl_mercado_cambios` had ~992 orphan rows (BCRA revisions created new ids
instead of updating), so `SUM(monto)` consumers such as `server/routes/indicadores.js:604-623`
double-counted. The table was migrated to stable ids; no change needed in crud_bonds, but any
figure taken from those sums before that date is wrong.

## Acceptance

For each rule, extract a small pure function (input: now, last heartbeat / max period) and
unit-test these cases:

- reca heartbeat:
  - 2026-10-01 06:00, last run 2026-09-10 20:00 → OK
  - 2026-10-01 21:00, same last run → FALLA
  - 2026-10-02 21:30, last run 2026-10-01 20:00 → FALLA
  - 2026-10-14 any time → no heartbeat check
  - 2026-10-16 10:00, last run 2026-10-10 20:00 → OK
  - 2026-10-16 22:30, same last run → FALLA
- rem:
  - 2026-10-01, max 2026-08-31 → OK
  - 2026-10-11, max 2026-08-31 → ATRASADO
  - 2026-10-11, max 2026-09-30 → OK

Scope: panel logic only. Don't touch ETL code, crontab, or DB.
