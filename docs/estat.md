# e-Stat catalog discovery

The first e-Stat layer deliberately discovers official statistical table IDs before hard-coding CPI, unemployment, industrial production, wages and household-spending series.

It uses the current e-Stat API v3.0 `getStatsList` endpoint and stores both raw JSON and a flattened Parquet catalog.

Once `ESTAT_APP_ID` is configured, the discovery workflow can run without further code changes. The resulting catalog will be used to pin exact `statsDataId` values before building the time-series collector.
