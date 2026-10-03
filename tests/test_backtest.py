import pandas as pd
import pytest

from analysis.backtest import (
    assign_quantiles,
    filter_asof,
    long_short_spread,
    quantile_summary,
)


def test_filter_asof_excludes_future_information():
    df = pd.DataFrame(
        {
            "known_at": [
                "2026-01-01T00:00:00Z",
                "2026-01-03T00:00:00Z",
            ],
            "value": [1, 2],
        }
    )
    out = filter_asof(df, "2026-01-02T00:00:00Z")
    assert out["value"].tolist() == [1]


def test_filter_asof_rejects_invalid_timestamp():
    df = pd.DataFrame({"known_at": ["not-a-date"], "value": [1]})
    with pytest.raises(ValueError):
        filter_asof(df, "2026-01-02T00:00:00Z")


def test_quantile_assignment_and_spread():
    rows = []
    for value in range(10):
        rows.append(
            {
                "date": "2026-01-31",
                "factor": value,
                "forward_return": value / 100.0,
            }
        )
    df = pd.DataFrame(rows)
    ranked = assign_quantiles(
        df,
        date_col="date",
        factor_col="factor",
        n_quantiles=5,
    )
    assert ranked["quantile"].min() == 1
    assert ranked["quantile"].max() == 5

    summary = quantile_summary(
        ranked,
        date_col="date",
        quantile_col="quantile",
        forward_return_col="forward_return",
    )
    spread = long_short_spread(summary, date_col="date")
    assert len(summary) == 5
    assert spread.loc[0, "long_short_return"] > 0


def test_high_is_good_false_reverses_preference():
    df = pd.DataFrame(
        {
            "date": ["2026-01-31"] * 4,
            "factor": [1.0, 2.0, 3.0, 4.0],
        }
    )
    ranked = assign_quantiles(
        df,
        date_col="date",
        factor_col="factor",
        n_quantiles=2,
        high_is_good=False,
    )
    preferred = ranked.loc[ranked["quantile"] == 2, "factor"]
    assert preferred.max() <= 2.0
