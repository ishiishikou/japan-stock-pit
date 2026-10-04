import numpy as np
import pandas as pd
import pytest

from analysis.factor_processing import (
    apply_transaction_costs,
    neutralize_factor,
    portfolio_turnover,
)


def test_neutralize_factor_removes_size_and_sector_components():
    n = 20
    size = np.exp(np.linspace(5.0, 10.0, n))
    sector = np.array(["A"] * 10 + ["B"] * 10)
    idiosyncratic = np.array([1, -1] * 10, dtype=float)
    raw = 2.5 * np.log(size) + (sector == "B") * 4.0 + idiosyncratic

    frame = pd.DataFrame(
        {
            "date": ["2026-01-31"] * n,
            "ticker": [f"{i:04d}" for i in range(n)],
            "factor": raw,
            "sector": sector,
            "market_cap": size,
        }
    )

    out = neutralize_factor(frame)
    scored = out.dropna(subset=["neutralized_factor"])

    assert len(scored) == n
    assert scored["neutralized_factor"].mean() == pytest.approx(0.0, abs=1e-10)
    assert scored["neutralized_factor"].std(ddof=0) == pytest.approx(1.0)
    corr = np.corrcoef(
        scored["neutralized_factor"],
        np.log(scored["market_cap"]),
    )[0, 1]
    assert corr == pytest.approx(0.0, abs=1e-10)
    assert (
        scored.groupby("sector")["neutralized_factor"].mean().abs().max()
        < 1e-10
    )


def test_portfolio_turnover_handles_entries_and_exits():
    weights = pd.DataFrame(
        [
            {"date": "2026-01-01", "ticker": "A", "weight": 0.5},
            {"date": "2026-01-01", "ticker": "B", "weight": 0.5},
            {"date": "2026-02-01", "ticker": "A", "weight": 0.2},
            {"date": "2026-02-01", "ticker": "C", "weight": 0.8},
        ]
    )

    out = portfolio_turnover(weights)

    assert pd.isna(out.iloc[0]["turnover"])
    # A changes 0.5->0.2, B 0.5->0, C 0->0.8: half of 1.6.
    assert out.iloc[1]["turnover"] == pytest.approx(0.8)


def test_transaction_costs_are_linear_in_turnover():
    returns = pd.DataFrame(
        [{"date": "2026-02-01", "gross_return": 0.02}]
    )
    turnover = pd.DataFrame(
        [{"date": "2026-02-01", "turnover": 0.8}]
    )

    out = apply_transaction_costs(returns, turnover, cost_bps=10)

    assert out.iloc[0]["transaction_cost"] == pytest.approx(0.0008)
    assert out.iloc[0]["net_return"] == pytest.approx(0.0192)


def test_turnover_rejects_duplicate_positions():
    weights = pd.DataFrame(
        [
            {"date": "2026-01-01", "ticker": "A", "weight": 0.5},
            {"date": "2026-01-01", "ticker": "A", "weight": 0.5},
        ]
    )

    with pytest.raises(ValueError):
        portfolio_turnover(weights)
