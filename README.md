# japan-stock-pit

Free-data point-in-time research infrastructure for Japanese equities.

## Goal

Collect as much useful market, fundamental, ownership, event and macro data as practical from free official sources, preserve when the information became knowable, and use it later for factor research, backtests and investor-behaviour analysis.

## Architecture

- **GitHub**: collectors, schemas, workflows and analysis code
- **Backblaze B2**: private raw and normalized data
- **Parquet**: primary normalized storage format
- **GitHub Actions**: scheduled collection
- **PIT principle**: keep `observed_at`, conservative `known_at`, source update time and revision history

See:

- [Free data source catalog](docs/data-sources.md)
- [Point-in-time conventions](docs/pit-conventions.md)
- [EDINET collector design](docs/edinet.md)

## Current status

### Completed

- B2 private bucket connectivity verified from GitHub Actions.
- B2 credentials stored only in GitHub Actions Secrets.
- Bank of Japan Time-Series API collector is running in production.
- Initial BOJ collection succeeded with **46,010 normalized PIT rows** across 5 series.
- Scheduled BOJ collection runs on weekdays at 18:15 JST.
- Raw BOJ API payloads are stored only when their content hash changes.
- Normalized current snapshots and new/revised change rows are stored separately.
- EDINET API Version 2 document-list collector implemented.
- EDINET raw JSON, normalized Parquet snapshots, revision logs and run manifests are designed for B2 storage.
- EDINET GitHub Actions workflow passed syntax validation and safely skips collection until `EDINET_API_KEY` is configured.
- Existing EDINET source URLs never include the API key.

### Initial BOJ series

- Uncollateralized overnight call rate
- USD/JPY spot at 17:00
- USD/JPY central rate
- Nominal effective exchange rate
- Real effective exchange rate

## Storage layout

```text
raw/
  boj_macro/
  edinet/
    document_list/

normalized/
  boj_macro/
    current.parquet
    changes/
  edinet/
    document_list/
      file_date=YYYY-MM-DD/
      changes/

features/

metadata/
  boj_macro/
  edinet/
```

## Repository configuration

### Actions secrets

Configured:

- `B2_KEY_ID`
- `B2_APPLICATION_KEY`

Pending for enabled collectors:

- `EDINET_API_KEY`

### Actions variables

- `B2_BUCKET_NAME`
- `B2_ENDPOINT`

Future free-source credentials will be added only when their collectors are implemented.

## Next sources / phases

1. Configure the free EDINET API key and validate the first production collection.
2. EDINET selective document retrieval — XBRL/CSV packages for filings useful to fundamentals and ownership research.
3. J-Quants free tier — listed-company master, prices and financial summaries within free-plan terms.
4. e-Stat API — CPI, employment, production and related macro series.
5. Derived feature layer — valuation, quality, momentum, liquidity, ownership and event features.
6. JPX public-site statistics only if a compliant acquisition/reuse path is confirmed; do not automate site downloads merely because they are publicly viewable.

## Security and source-use policy

Never commit API keys, B2 credentials, paid data, or private B2 objects to this public repository.

Public availability does not automatically mean automated collection or secondary use is permitted. Source terms take priority over collection coverage.
