# EDINET historical backfill

Historical package backfill is intentionally stateful and budget-aware.

## Phase order

1. ownership — document types 350/360
2. annual financial — 120/130
3. periodic financial — 140/150/160/170
4. extraordinary reports / treasury-share reports — 180/190/220
5. tender-offer related filings

Each phase walks backward from the edge of the seven-day current-data window toward approximately ten years of history. Existing per-document manifests make the process idempotent.

## Guard rails

- 14 historical dates per scheduled run
- at most 75 new document packages per run
- 1.5 seconds between package downloads
- twice-daily schedule
- B2 storage report checked before each run
- at 85%+ of the 10 GiB reference budget, backfill pauses
- at 70–85%, only ownership backfill is allowed

State is persisted at `metadata/edinet/backfill/state.json`, so interrupted runs continue rather than restart.
