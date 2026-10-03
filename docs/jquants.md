# J-Quants Free collector

The collector uses the official `jquants-api-client` V2 client and API-key authentication.

## Free-plan scope used here

- Listed Issue Master
- Stock Prices (OHLC)
- Financial Data (Summary)

The Free plan is delayed by 12 weeks and has a low request-rate allowance, so the scheduled collector intentionally makes only three logical dataset calls per day for one delayed target date.

## Secret

`JQUANTS_API_KEY`

The workflow safely skips data collection until the secret is configured.

## PIT note

J-Quants adjusted prices can be retroactively adjusted after corporate actions. The archive preserves the provider fields, but strict PIT momentum research must not blindly substitute retroactively adjusted values for values that were historically known at the decision time.
