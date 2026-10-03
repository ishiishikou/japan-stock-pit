# Free data source catalog

Last reviewed: 2026-10-04

This project prefers official, free, automation-friendly sources. Data files themselves remain private in Backblaze B2.

| Source | Scope | Automation | Credentials | Planned cadence | PIT value | Notes |
|---|---|---|---|---|---|---|
| Bank of Japan Time-Series API | rates, FX, money, Tankan, flow of funds, macro | enabled | none | weekdays | high | Official JSON/CSV API. Avoid high-frequency access. |
| EDINET API Version 2 | filings, XBRL, large-shareholding reports | collector implemented; activation pending | free API key | daily | very high | Official API. Document-list collector is implemented; selective XBRL/CSV retrieval is next. |
| J-Quants | listed master, prices, financial summaries | planned | free account/token | daily/weekly | high | Use only within current free-plan terms, delay and history limits. |
| e-Stat API | CPI, employment, production and government statistics | planned | free app ID | release cadence | medium/high | Official government statistics API. |
| JPX public website statistics | margin, short-selling, investor-type flows, market stats | **deferred** | none | n/a | potentially high | JPX site terms restrict secondary use/re-distribution and ask users to avoid high-frequency/high-load automated acquisition. Do not automate website downloads until an authorized/compliant path is confirmed. |
| TDnet free viewing service | timely disclosures | disabled for scraping | none | n/a | high | JPX explicitly asks users not to use scraping for automated acquisition. |

## Priority order

1. Sources with explicit official APIs and clear machine-readable access.
2. Event data that cannot be reliably reconstructed later.
3. Daily market/flow data with clear reuse terms.
4. Slowly changing fundamentals and metadata.
5. Sources with restrictive or ambiguous automation/reuse terms only after a compliant acquisition path is identified.

## Storage layers

- `raw/`: source responses or source documents, deduplicated by content hash where practical.
- `normalized/`: typed Parquet with PIT metadata.
- `features/`: derived factors such as momentum, valuation and quality.
- `metadata/`: manifests, hashes, schemas and ingestion state.

## PIT metadata policy

Every normalized dataset should preserve, where available:

- source-native record/document identifier
- `observed_at`
- conservative `known_at`
- provider processing/update timestamp
- source URL without credentials
- source payload hash
- record hash/version identifier
- ingestion run ID

## Credentials

Already configured:

- `B2_KEY_ID`
- `B2_APPLICATION_KEY`
- `B2_BUCKET_NAME`
- `B2_ENDPOINT`

Needed next:

- `EDINET_API_KEY`

Future free-source credentials:

- `ESTAT_APP_ID`
- J-Quants credentials/token variables once its current authentication flow is implemented.
