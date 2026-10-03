# Free data source catalog

Last reviewed: 2026-10-04

This project prefers official, free, automation-friendly sources. Data files themselves remain private in Backblaze B2.

| Source | Scope | Automation | Credentials | Cadence | PIT value | Status / notes |
|---|---|---|---|---|---|---|
| Bank of Japan Time-Series API | rates, FX, money, Tankan, flow of funds, macro | enabled | none | weekdays | high | Production collector active. Initial 46,010 normalized rows across 5 series. More series planned. |
| EDINET API Version 2 | filings, XBRL-to-CSV, large-shareholding reports | enabled | free API key | daily + twice-daily package/backfill jobs | very high | Production collection active. Document list, selective packages, ownership/financial normalization and stateful historical backfill implemented. |
| J-Quants API V2 Free | listed master, OHLC, financial summaries | ready; blocked only on secret | free account/API key | daily | high | Collector/workflow implemented. Free plan is delayed; raw redistribution is not placed in the public repo. Waiting for `JQUANTS_API_KEY`. |
| e-Stat API v3 | CPI, employment, production, wages and government statistics | ready; blocked only on secret | free Application ID | weekly discovery, then release cadence | medium/high | Catalog-discovery collector/workflow implemented. Waiting for `ESTAT_APP_ID`. |
| JPX public website statistics | margin, short-selling, investor-type flows, market stats | **deferred** | none | n/a | potentially high | Do not automate website downloads until an authorized/compliant acquisition and reuse path is confirmed. |
| TDnet free viewing service | timely disclosures | disabled for scraping | none | n/a | high | Do not automate the free viewing site. Use official APIs/other compliant sources instead. |

## Priority order

1. Sources with explicit official APIs and clear machine-readable access.
2. Event data that cannot be reliably reconstructed later.
3. Daily market/flow data with clear reuse terms.
4. Slowly changing fundamentals and metadata.
5. Sources with restrictive or ambiguous automation/reuse terms only after a compliant path is identified.

## Storage layers

- `raw/`: source responses/packages, deduplicated by content hash where practical.
- `normalized/`: typed Parquet with PIT metadata.
- `features/`: derived factors such as momentum, valuation and quality.
- `metadata/`: manifests, hashes, ingestion state, storage budget and health reports.

## PIT metadata policy

Every normalized dataset should preserve, where available:

- source-native record/document identifier
- `observed_at`
- conservative `known_at`
- provider processing/update timestamp
- source URL without credentials
- source payload/package hash
- record/version identifier
- ingestion run ID
- correction / parent-document relationship where relevant

## Credentials

Configured:

- `B2_KEY_ID`
- `B2_APPLICATION_KEY`
- `B2_BUCKET_NAME`
- `B2_ENDPOINT`
- `EDINET_API_KEY`

Pending user registration:

- `ESTAT_APP_ID`
- `JQUANTS_API_KEY`
