# PIT research workflow

The archive is built so that research queries use `known_at`, never "latest data now".

## Reconstruction rule

For a historical decision time `T`:

1. exclude every record with `known_at > T`
2. within the remaining records, select the newest version for the logical fact
3. preserve revisions rather than overwriting historical knowledge
4. calculate features from the reconstructed view
5. only then join forward returns

`analysis/pit_asof.sql` contains the base SQL pattern.

## Validation order

1. single-factor coverage and missingness
2. cross-sectional quantiles
3. forward 1M/3M/6M/12M returns
4. turnover and liquidity constraints
5. sector/size neutralization
6. combinations of surviving factors
7. investor-action comparison
8. ML only after simpler tests are stable
