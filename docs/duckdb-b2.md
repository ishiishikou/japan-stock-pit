# Query private Backblaze B2 Parquet with DuckDB

DuckDB's `httpfs` extension can query S3-compatible object storage directly.
Backblaze B2 exposes an S3-compatible HTTPS endpoint, so the private PIT
Parquet files can be queried without first copying the whole data lake to disk.

Do **not** save real credentials in this repository or in a checked-in SQL
file. Substitute credentials only in a local/session-scoped secret.

## Session example

```sql
INSTALL httpfs;
LOAD httpfs;

CREATE OR REPLACE SECRET b2_pit (
    TYPE s3,
    PROVIDER config,
    KEY_ID '<B2_KEY_ID>',
    SECRET '<B2_APPLICATION_KEY>',
    REGION 'us-east-005',
    ENDPOINT 's3.us-east-005.backblazeb2.com',
    URL_STYLE 'path',
    USE_SSL true,
    SCOPE 's3://japan-stock-pit-data/'
);
```

Backblaze accepts both virtual-host and path-style S3 URLs; path style is
explicit here because it makes the custom endpoint behavior unambiguous.

## Read a partitioned dataset

```sql
SELECT *
FROM read_parquet(
    's3://japan-stock-pit-data/normalized/edinet/financial_metrics/**/*.parquet',
    hive_partitioning = true,
    union_by_name = true
)
LIMIT 100;
```

## PIT filter example

```sql
WITH facts AS (
    SELECT *
    FROM read_parquet(
        's3://japan-stock-pit-data/normalized/edinet/financial_metrics/**/*.parquet',
        hive_partitioning = true,
        union_by_name = true
    )
),
eligible AS (
    SELECT *
    FROM facts
    WHERE CAST(known_at AS TIMESTAMPTZ)
          <= TIMESTAMPTZ '2026-01-31 15:00:00+09:00'
)
SELECT *
FROM eligible;
```

A research query should apply the PIT filter before deriving features or
joining forward returns.

## Operational notes

- Keep the B2 bucket private.
- Prefer read-only B2 application keys for local research once the ingestion
  pipeline is stable.
- Use Parquet partition pruning rather than downloading all objects.
- The current ingestion key is bucket-scoped; do not expose it in notebooks,
  screenshots, logs, or shell history.
