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

## Current status

### Completed

- B2 private bucket connectivity verified from GitHub Actions.
- B2 credentials stored only in GitHub Actions Secrets.
- First production collector implemented for the Bank of Japan Time-Series API.
- Initial BOJ collection succeeded with **46,010 normalized PIT rows** across 5 series.
- Scheduled BOJ collection runs on weekdays at 18:15 JST.
- Raw BOJ API payloads are stored only when their content hash changes.
- Normalized current snapshot and new/revised change rows are stored separately.

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

normalized/
  boj_macro/
    current.parquet
    changes/

features/

metadata/
  boj_macro/
```

## Repository configuration

### Actions secrets

- `B2_KEY_ID`
- `B2_APPLICATION_KEY`

### Actions variables

- `B2_BUCKET_NAME`
- `B2_ENDPOINT`

Future free-source credentials will be added only when their collectors are implemented.

## Next sources

1. EDINET API — filings, XBRL, large-shareholding reports
2. J-Quants free tier — listed-company master, prices and financial summaries within free-plan terms
3. e-Stat API — CPI, employment, production and related macro series
4. Selected JPX statistics — only where acquisition is compliant and low-frequency
5. Derived factor layer — valuation, quality, momentum, liquidity, ownership and event features

## Security

Never commit API keys, B2 credentials, paid data, or private B2 objects to this public repository.
