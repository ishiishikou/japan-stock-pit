import numpy as np
import pandas as pd
import pytest

from features.market import build_forward_returns, build_market_features


def sample_bars(n=270):
    dates = pd.bdate_range("2025-01-01", periods=n)
    prices = 100.0 + np.arange(n, dtype=float)
    return pd.DataFrame(
        {
            "Date": dates.strftime("%Y-%m-%d"),
            "Code": ["72030"] * n,
            "C": prices,
            "AdjC": prices,
            "Va": [1_000_000_000.0] * n,
            "known_at": [f"{d:%Y-%m-%d}T06:00:00Z" for d in dates],
            "observed_at": [
                (d + pd.Timedelta(days=84)).strftime("%Y-%m-%dT00:00:00Z")
                for d in dates
            ],
        }
    )


def test_market_features_build_12_1_momentum_and_liquidity():
    bars = sample_bars()
    out = build_market_features(bars)

    last_date = bars.iloc[-1]["Date"]
    final = out.loc[out["date"] == last_date].set_index("feature")

    expected_momentum = (
        bars["AdjC"].iloc[-1 - 21] / bars["AdjC"].iloc[-1 - 252] - 1
    )
    assert final.loc["momentum_12_1", "value"] == pytest.approx(expected_momentum)
    assert final.loc["avg_trading_value_20d", "value"] == pytest.approx(1_000_000_000.0)
    assert final.loc["amihud_20d", "value"] > 0
    assert final.loc["momentum_12_1", "known_at"] == bars.iloc[-1 - 21]["known_at"]


def test_market_features_fall_back_to_unadjusted_close():
    bars = sample_bars(30).drop(columns=["AdjC"])
    out = build_market_features(
        bars,
        momentum_lookback_days=25,
        momentum_skip_days=5,
        liquidity_window=5,
    )
    assert "momentum_12_1" in out["feature"].tolist()


def test_forward_returns_use_future_bar_as_outcome_availability():
    bars = sample_bars(80)
    out = build_forward_returns(bars, horizons=(21, 63))

    first_21 = out.loc[
        (out["ticker"] == "7203")
        & (out["date"] == bars.iloc[0]["Date"])
        & (out["horizon_trading_days"] == 21)
    ].iloc[0]

    expected = bars["AdjC"].iloc[21] / bars["AdjC"].iloc[0] - 1
    assert first_21["forward_return"] == pytest.approx(expected)
    assert first_21["outcome_known_at"] == bars.iloc[21]["known_at"]
    assert first_21["outcome_observed_at"] == bars.iloc[21]["observed_at"]


def test_forward_returns_reject_nonpositive_horizon():
    with pytest.raises(ValueError):
        build_forward_returns(sample_bars(30), horizons=(0,))
