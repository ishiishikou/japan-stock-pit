# japan-stock-pit

Point-in-time data collection and research infrastructure for Japanese equities.

## Storage design

- GitHub: collection code, schemas, workflows, and analysis logic
- Backblaze B2: private point-in-time datasets
- Data format: primarily Parquet
- Credentials: GitHub Actions Secrets only; never committed to this repository

## B2 connection test

The `B2 Smoke Test` GitHub Actions workflow uploads a small text object to the configured private B2 bucket and verifies it with `head_object`.

Required repository configuration:

### Actions secrets

- `B2_KEY_ID`
- `B2_APPLICATION_KEY`

### Actions variables

- `B2_BUCKET_NAME`
- `B2_ENDPOINT`
