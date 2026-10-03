# EDINET API collector

EDINET API Version 2 is the primary free source for Japanese statutory filings and ownership disclosures in this project.

## Current implementation

`collectors/edinet_documents.py` collects the complete document-list metadata for each requested EDINET file date and stores:

- raw JSON responses in B2, deduplicated by SHA-256
- normalized Parquet snapshots by EDINET file date
- new/revised record versions in an append-only PIT change log
- run manifests in `metadata/edinet/runs/`

No API key is written to logs, source URLs, Parquet, or the public repository.

## Required GitHub Actions secret

- `EDINET_API_KEY`

The API key is free but must be issued through EDINET's API registration flow.

## PIT handling

For ordinary submitted documents, `known_at` uses `submitDateTime`.

If EDINET exposes `opeDateTime`, the later of submission and operation time is used. For a record revision detected without an exact edit timestamp, the collector uses our `observed_at` time as the conservative PIT boundary.

The collector revisits the most recent three JST calendar days by default to tolerate scheduler delays and late metadata updates. EDINET also publishes operation-date records for withdrawals/disclosure changes, so those events can be preserved even when an older original file date is not rescanned every day.

## Next EDINET phases

1. Select filing classes relevant to listed equities.
2. Download XBRL/CSV packages only for documents that add useful fundamental or ownership data.
3. Extract normalized financial statements, forecasts where available, large-shareholding reports and amendment relationships.
4. Add controlled historical backfill without exceeding B2 storage or API load.
