# japan-stock-pit

Free-data point-in-time research infrastructure for Japanese equities.

## Goal

Collect as much useful market, fundamental, ownership, event and macro data as practical from free official sources, preserve when the information became knowable, and use it later for factor research, backtests and investor-behaviour analysis.

## Architecture

- **GitHub**: collectors, processors, feature contracts, workflows and research code
- **Backblaze B2**: private raw, normalized and metadata storage
- **Parquet**: primary normalized storage format
- **GitHub Actions**: scheduled collection, normalization, health checks and tests
- **PIT principle**: preserve `observed_at`, conservative `known_at`, source timestamps, hashes and revision history

See:

- [Free data source catalog](docs/data-sources.md)
- [Point-in-time conventions](docs/pit-conventions.md)
- [EDINET collector design](docs/edinet.md)
- [EDINET package collection](docs/edinet-packages.md)
- [EDINET ownership normalization](docs/edinet-ownership.md)
- [EDINET financial metrics](docs/edinet-financial.md)
- [Historical backfill](docs/edinet-backfill.md)
- [DuckDB → B2 querying](docs/duckdb-b2.md)
- [Research workflow](docs/research-workflow.md)
- [Minimal backtest design](docs/backtesting.md)

## Current verified status

### Production collection

- Backblaze B2 connectivity from GitHub Actions is verified.
- Bank of Japan Time-Series API collector is running in production.
  - Initial BOJ collection: **46,010 normalized PIT rows** across 5 series.
  - Scheduled weekdays at 18:15 JST.
- EDINET API Version 2 collection is running in production.
  - Document-list PIT collection is active.
  - Selective XBRL-to-CSV package collection is active.
  - Initial package run: **150 filings / 92,536 generic XBRL fact rows / 0 package failures**.
  - Ownership normalization has reached **122 documents / 345 output rows / 0 failures** across the verified initial + catch-up runs.
  - Initial canonical financial normalization: **78 documents / 8,998 metric rows / 0 failures**.
  - Historical backfill is stateful and budget-aware.
  - Initial backfill run: **75 ownership documents processed / 0 failures**; cursor advanced to **2026-09-25**.

### Storage and quality controls

- Latest measured B2 usage: **0.0271 GiB / 10 GiB reference budget**.
- Latest object count: **1,103**.
- Storage level: **ok**.
- PIT data-health workflow is enabled.
- Unit-test workflow is enabled and currently passing.
- Collection jobs use idempotent per-document manifests and content hashes where practical.

### Ready but waiting for free credentials

- **J-Quants API V2**
  - Collector and workflow implemented.
  - Waiting for `JQUANTS_API_KEY`.
- **e-Stat API**
  - Catalog-discovery collector and workflow implemented.
  - Waiting for `ESTAT_APP_ID`.

Both workflows safely skip collection while their secrets are absent.

## Research layers

Implemented:

- Generic EDINET XBRL fact layer
- Ownership summary layer
- Canonical financial metric layer
- Common PIT feature contract
- PIT as-of SQL examples
- Minimal cross-sectional quantile backtest helpers
- DuckDB direct-query guide for private B2 Parquet

Planned next:

- ROE / ROIC / FCF conversion / net debt feature generators
- management forecast revision and capital-allocation features
- price, momentum and liquidity layers after J-Quants activation
- additional BOJ macro series
- e-Stat macro time-series pinning after catalog discovery
- sector/size neutralization, turnover and transaction-cost-aware backtests

## Storage layout

```text
raw/
  boj_macro/
  edinet/
    document_list/
    csv_packages/
  estat/                # after activation

normalized/
  boj_macro/
  edinet/
    document_list/
    xbrl_facts/
    ownership_summary/
    financial_metrics/
  jquants/              # after activation
  estat/                # after activation

features/

metadata/
  boj_macro/
  edinet/
    packages/
    ownership/
    financial_metrics/
    backfill/
  storage/
  health/
  jquants/
  estat/
```

## Repository configuration

### Actions secrets — configured

- `B2_KEY_ID`
- `B2_APPLICATION_KEY`
- `EDINET_API_KEY`

### Actions variables — configured

- `B2_BUCKET_NAME`
- `B2_ENDPOINT`

### Actions secrets — pending user registration

- `ESTAT_APP_ID`
- `JQUANTS_API_KEY`

See GitHub Issue #5 for the current user-action checklist.

## Security and source-use policy

Never commit API keys, B2 credentials, paid data, or private B2 objects to this public repository.

Public availability does not automatically mean automated collection or secondary use is permitted. Source terms take priority over collection coverage. TDnet free-view pages are not scraped, and JPX public-site statistics remain deferred until a compliant acquisition/reuse path is established.
