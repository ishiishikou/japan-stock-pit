# Minimal PIT backtest helpers

`analysis/backtest.py` provides source-agnostic primitives for the first
cross-sectional validation stage.

The intended order is:

1. reconstruct a historical view using `known_at <= decision_time`
2. calculate a factor from that reconstructed view
3. assign cross-sectional quantiles at the decision date
4. attach forward returns that occur strictly after the decision date
5. compare quantile returns and the preferred-high minus low spread

The helper intentionally does **not** fetch data, select securities, fill
missing fundamentals, neutralize sectors, or calculate transaction costs.
Those choices should remain explicit research decisions rather than hidden
inside a convenience backtester.

Before treating any factor result as evidence, later layers should add:

- minimum liquidity filters
- delisting/survivorship handling
- sector and size neutralization
- realistic rebalance lag
- corporate-action-safe return construction
- turnover / transaction-cost estimates
- multiple-testing awareness
