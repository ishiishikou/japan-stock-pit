# EDINET document package collection

This layer consumes the EDINET document-list API and selectively downloads the official XBRL-to-CSV ZIP package (`documents/{docID}?type=5`) for research-relevant filings.

## Why CSV ZIP is the default raw package

EDINET's official viewer guide states that the XBRL-to-CSV output contains all filing items except presentation/layout information. Each row carries nine fields:

1. element ID
2. item name
3. context ID
4. relative year
5. consolidated / non-consolidated
6. period / instant
7. unit ID
8. human-readable unit
9. value

For factor and ownership research this retains the facts needed for reproducible extraction while consuming much less storage than duplicating both XBRL and CSV for every filing.

## Current categories

Configured in `config/edinet_document_types.json`:

- financial: 120/130/140/150/160/170
- event: 180/190
- buyback: 220
- tender-offer related: 240/250/270/280/290/300/310/320
- ownership: 350/360

Only records with `csvFlag=1` are downloaded.

## Storage

```text
raw/edinet/csv_packages/
  category=<category>/doc_type=<code>/submit_date=YYYY-MM-DD/
    <docID>_<sha256>.zip

normalized/edinet/xbrl_facts/
  category=<category>/doc_type=<code>/submit_date=YYYY-MM-DD/
    <docID>.parquet

metadata/edinet/packages/
  doc_id=<docID>.json

metadata/edinet/package_runs/
  year=YYYY/month=MM/day=DD/<run_id>.json
```

A per-document manifest makes collection idempotent: already-normalized `docID` values are skipped on later runs.

## PIT rule

`known_at` is the later of EDINET's submission timestamp and operation timestamp when available. If neither exists, the collector falls back to our `observed_at`.

## Load control

The workflow runs twice per day, scans the latest seven JST calendar days, downloads at most 150 new packages per run, and sleeps 1.5 seconds between document downloads. Failed documents are written to a separate metadata error path and do not stop the rest of the batch.
