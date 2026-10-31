# EDINET canonical financial metrics

The generic XBRL fact layer is intentionally broad. This processor adds a conservative derived layer that maps **exact XBRL local names** to a small canonical metric vocabulary.

It does not try to choose a single "correct" annual value yet. Instead it preserves context, relative year, consolidation flag, period/instant, units, raw value, and parsed numeric value.

That design avoids hiding ambiguity and keeps PIT research reproducible.

## Initial canonical metrics

Revenue, operating income, ordinary income, pretax income, net income, assets, liabilities, net assets, shareholders' equity, cash, interest-bearing debt, operating/investing/financing cash flow, depreciation, capital expenditure, R&D, EPS, dividend per share, and shares outstanding.

Aliases live in `config/financial_metric_elements.json` and are designed to be expanded transparently as real EDINET documents reveal additional taxonomy variants.

## Storage

```text
normalized/edinet/financial_metrics/
  submit_date=YYYY-MM-DD/<docID>.parquet

metadata/edinet/financial_metrics/
  doc_id=<docID>.json
```

The generic `normalized/edinet/xbrl_facts/` layer remains the source of truth.


## Backblaze B2 Class B read budget

Financial normalization previously read every per-document manifest (one `GetObject`
per known financial document on every daily run), even when already normalized.
The 2026-10-31 fix maintains:

- `metadata/edinet/financial_metric_index/v1.json` — a consolidated index of
  per-document status, source key/ETag/size, and the alias configuration version
  and SHA-256.
- Existing `metadata/edinet/financial_metrics/doc_id=<docID>.json` manifests
  remain intact; they are the compatibility and auditing records.
- A normal, fully indexed run reads **one index object** instead of all
  per-document manifest objects. LIST requests still enumerate source and
  legacy manifest keys.
- An initial bounded migration (default **150 documents/run**) validates old
  manifests before populating index entries. It prioritizes newer filings and
  is resumable (checkpoint every 25 items). If a source ETag, size, alias
  version, or config SHA changes, the source must be reprocessed.
- `EDINET_FINANCIAL_MAX_DOCS_PER_RUN` controls the 1..2000 upper bound for
  pending checks / reprocessing. Do not raise it without confirming Class B
  usage and the account's daily free cap.

The financial job's JSON output records `legacy_manifest_gets`,
`pending_documents` and an `b2_api_usage` event with
`GetObject`, `ListObjectsV2`, `PutObject` counters. These counts are
SDK-side operations, **not** an authoritative Backblaze billing statement.
Package collection and historical backfill also print their SDK operation
counters on exit.

To prevent surprise full data collection on merges, financial, package, and
backfill workflows now trigger on schedule or manual dispatch, not on every
source push. While observing recurring Class B caps, package and backfill
collection run once daily. Keep B2 account caps enabled; no paid increase
is implied by this change.
