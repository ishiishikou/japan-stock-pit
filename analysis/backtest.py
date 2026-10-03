#!/usr/bin/env python3
"""Minimal point-in-time cross-sectional backtest helpers.

These functions deliberately stay source-agnostic. They assume feature rows have
already been reconstructed using known_at <= decision_time.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True)
class BacktestColumns:
    date: str = "date"
    ticker: str = "ticker"
    factor: str = "factor"
    forward_return: str = "forward_return"
    known_at: str = "known_at"


def filter_asof(
    frame: pd.DataFrame,
    decision_time,
    known_at_col: str = "known_at",
) -> pd.DataFrame:
    """Return only information provably known by decision_time."""
    if known_at_col not in frame.columns:
        raise KeyError(f"missing PIT column: {known_at_col}")
    out = frame.copy()
    known = pd.to_datetime(out[known_at_col], utc=True, errors="coerce")
    if known.isna().any():
        bad = int(known.isna().sum())
        raise ValueError(f"{bad} rows have invalid {known_at_col}")
    decision = pd.Timestamp(decision_time)
    if decision.tzinfo is None:
        decision = decision.tz_localize("UTC")
    else:
        decision = decision.tz_convert("UTC")
    return out.loc[known <= decision].copy()


def assign_quantiles(
    frame: pd.DataFrame,
    *,
    date_col: str,
    factor_col: str,
    n_quantiles: int = 5,
    high_is_good: bool = True,
    output_col: str = "quantile",
) -> pd.DataFrame:
    """Assign deterministic cross-sectional quantiles within each date.

    Quantile 1 is the low-factor bucket and quantile N is the high-factor
    bucket when high_is_good=True. When high_is_good=False, the factor sign is
    reversed first, so quantile N is still the preferred bucket.
    """
    if n_quantiles < 2:
        raise ValueError("n_quantiles must be >= 2")
    for col in (date_col, factor_col):
        if col not in frame.columns:
            raise KeyError(f"missing column: {col}")

    out = frame.copy()
    out[output_col] = pd.NA

    for _, idx in out.groupby(date_col, sort=False).groups.items():
        group = out.loc[idx]
        values = pd.to_numeric(group[factor_col], errors="coerce")
        eligible = values.notna()
        if int(eligible.sum()) < n_quantiles:
            continue

        score = values.loc[eligible]
        if not high_is_good:
            score = -score

        # Rank first so ties do not make qcut drop bins unpredictably.
        ranks = score.rank(method="first")
        labels = pd.qcut(ranks, q=n_quantiles, labels=False) + 1
        out.loc[labels.index, output_col] = labels.astype("Int64")

    out[output_col] = out[output_col].astype("Int64")
    return out


def quantile_summary(
    frame: pd.DataFrame,
    *,
    date_col: str,
    quantile_col: str,
    forward_return_col: str,
) -> pd.DataFrame:
    """Summarize realized forward returns by date and factor quantile."""
    for col in (date_col, quantile_col, forward_return_col):
        if col not in frame.columns:
            raise KeyError(f"missing column: {col}")

    work = frame.dropna(subset=[quantile_col, forward_return_col]).copy()
    work[forward_return_col] = pd.to_numeric(
        work[forward_return_col], errors="coerce"
    )
    work = work.dropna(subset=[forward_return_col])
    if work.empty:
        return pd.DataFrame(
            columns=[date_col, quantile_col, "count", "mean_return", "median_return"]
        )

    result = (
        work.groupby([date_col, quantile_col], observed=True)[forward_return_col]
        .agg(count="count", mean_return="mean", median_return="median")
        .reset_index()
    )
    return result


def long_short_spread(
    summary: pd.DataFrame,
    *,
    date_col: str,
    quantile_col: str = "quantile",
    mean_return_col: str = "mean_return",
) -> pd.DataFrame:
    """Compute preferred-high minus low bucket return per date."""
    if summary.empty:
        return pd.DataFrame(columns=[date_col, "long_short_return"])
    required = {date_col, quantile_col, mean_return_col}
    missing = required - set(summary.columns)
    if missing:
        raise KeyError(f"missing columns: {sorted(missing)}")

    work = summary.dropna(subset=[quantile_col, mean_return_col]).copy()
    qmin = work.groupby(date_col)[quantile_col].transform("min")
    qmax = work.groupby(date_col)[quantile_col].transform("max")
    low = (
        work.loc[work[quantile_col] == qmin, [date_col, mean_return_col]]
        .rename(columns={mean_return_col: "low_return"})
    )
    high = (
        work.loc[work[quantile_col] == qmax, [date_col, mean_return_col]]
        .rename(columns={mean_return_col: "high_return"})
    )
    merged = high.merge(low, on=date_col, how="inner")
    merged["long_short_return"] = merged["high_return"] - merged["low_return"]
    return merged[[date_col, "long_short_return"]].sort_values(date_col).reset_index(drop=True)
