# Free data source catalog

Last reviewed: 2026-10-04

This project prefers official, free, automation-friendly sources. Data files themselves remain private in Backblaze B2.

| Source | Scope | Automation | Credentials | Planned cadence | PIT value | Notes |
|---|---|---|---|---|---|---|
| Bank of Japan Time-Series API | rates, FX, money, Tankan, flow of funds, macro | enabled | none | weekdays | high | Official JSON/CSV API. Avoid high-frequency access. |
| EDINET API | filings, XBRL, large-shareholding reports | planned | free API key | daily | very high | Primary source for corporate filings and ownership events. |
| J-Quants | listed master, prices, financial summaries | planned | free account/token | daily/weekly | high | Use only within current free-plan terms and retention limits. |
| e-Stat API | CPI, employment, production and government statistics | planned | free app ID | release cadence | medium/high | Official government statistics API. |
| JPX public statistics pages | margin, short-selling, investor-type flows, market stats | cautious | none | low frequency only | high | JPX asks users to avoid high-frequency/high-load automated acquisition; data stays private and is not redistributed. |
| TDnet free viewing service | timely disclosures | disabled for scraping | none | n/a | high | Do not automate the free viewing site; JPX explicitly asks users not to use scraping for automated acquisition. |

## Priority order

1. Sources with explicit official APIs and clear machine-readable access.
2. Event data that cannot be reliably reconstructed later.
3. Daily market/flow data.
4. Slowly changing fundamentals and metadata.
5. Sources with restrictive or ambiguous automation terms only after a compliant acquisition path is identified.

## Storage layers

- `raw/`: source responses or source documents, deduplicated by content hash where practical.
- `normalized/`: typed Parquet with PIT metadata.
- `features/`: derived factors such as momentum, valuation and quality.
- `metadata/`: manifests, hashes, schemas and ingestion state.

## Planned secrets / variables

Already configured:

- `B2_KEY_ID`
- `B2_APPLICATION_KEY`
- `B2_BUCKET_NAME`
- `B2_ENDPOINT`

Future free-source credentials:

- `EDINET_API_KEY`
- `ESTAT_APP_ID`
- J-Quants credentials/token variables once its current authentication flow is implemented.
