"""Market-derived PIT features and realized forward-return labels."""

from __future__ import annotations

import numpy as np
import pandas as pd

from features.contracts import FeatureDefinition, validate_feature_frame
from processors.identifiers import normalize_security_code


FEATURE_DEFINITIONS = {
    "momentum_12_1": FeatureDefinition(
        name="momentum_12_1",
        higher_is_better=True,
        source_datasets=("daily_bars",),
        description=(
            "Adjusted-price return from 252 trading days ago to 21 trading days ago; "
            "the most recent month is intentionally skipped."
        ),
    ),
    "amihud_20d": FeatureDefinition(
        name="amihud_20d",
        higher_is_better=False,
        source_datasets=("daily_bars",),
        description=(
            "20-trading-day mean of absolute daily return divided by trading value. "
            "Higher values indicate lower liquidity."
        ),
    ),
    "avg_trading_value_20d": FeatureDefinition(
        name="avg_trading_value_20d",
        higher_is_better=True,
        source_datasets=("daily_bars",),
        description="20-trading-day mean trading value.",
    ),
}


def _prepare_bars(bars: pd.DataFrame) -> pd.DataFrame:
    required = {"Date", "Code", "known_at", "observed_at", "Va"}
    missing = sorted(required - set(bars.columns))
    if missing:
        raise ValueError(f"missing J-Quants daily-bar columns: {missing}")
    if "AdjC" not in bars.columns and "C" not in bars.columns:
        raise ValueError("daily bars require AdjC or C")

    work = bars.copy()
    work["_ticker"] = work["Code"].map(normalize_security_code)
    work["_date"] = pd.to_datetime(work["Date"], errors="coerce")
    work["_known"] = pd.to_datetime(work["known_at"], utc=True, errors="coerce")
    work["_observed"] = pd.to_datetime(work["observed_at"], utc=True, errors="coerce")

    adjusted = (
        pd.to_numeric(work["AdjC"], errors="coerce")
        if "AdjC" in work.columns
        else pd.Series(np.nan, index=work.index)
    )
    close = (
        pd.to_numeric(work["C"], errors="coerce")
        if "C" in work.columns
        else pd.Series(np.nan, index=work.index)
    )
    work["_price"] = adjusted.where(adjusted.notna(), close)
    work["_trading_value"] = pd.to_numeric(work["Va"], errors="coerce")
    work.loc[work["_price"] <= 0, "_price"] = np.nan
    work.loc[work["_trading_value"] <= 0, "_trading_value"] = np.nan

    work = work.loc[
        work["_ticker"].notna()
        & work["_date"].notna()
        & work["_known"].notna()
        & work["_observed"].notna()
    ].copy()
    return work.sort_values(["_ticker", "_date"], kind="stable").reset_index(drop=True)


def build_market_features(
    bars: pd.DataFrame,
    *,
    momentum_lookback_days: int = 252,
    momentum_skip_days: int = 21,
    liquidity_window: int = 20,
) -> pd.DataFrame:
    """Build lagged momentum and rolling-liquidity features."""
    if momentum_lookback_days <= momentum_skip_days:
        raise ValueError("momentum lookback must exceed skip days")
    if liquidity_window < 2:
        raise ValueError("liquidity_window must be >= 2")

    work = _prepare_bars(bars)
    rows = []

    for ticker, group in work.groupby("_ticker", sort=False):
        group = group.copy().reset_index(drop=True)
        price = group["_price"]
        daily_return = price.pct_change(fill_method=None)

        group["_momentum"] = (
            price.shift(momentum_skip_days) / price.shift(momentum_lookback_days) - 1
        )
        group["_momentum_known"] = group["_known"].shift(momentum_skip_days)
        group["_momentum_observed"] = group["_observed"].shift(momentum_skip_days)

        illiq_daily = daily_return.abs() / group["_trading_value"]
        group["_amihud"] = illiq_daily.rolling(
            liquidity_window, min_periods=liquidity_window
        ).mean()
        group["_avg_value"] = group["_trading_value"].rolling(
            liquidity_window, min_periods=liquidity_window
        ).mean()

        observed_ns = group["_observed"].astype("int64")
        group["_liq_observed"] = pd.to_datetime(
            observed_ns.rolling(
                liquidity_window, min_periods=liquidity_window
            ).max(),
            utc=True,
        )

        for _, item in group.iterrows():
            date = item["_date"].date().isoformat()

            if pd.notna(item["_momentum"]):
                rows.append(
                    {
                        "date": date,
                        "ticker": ticker,
                        "feature": "momentum_12_1",
                        "value": float(item["_momentum"]),
                        "known_at": item["_momentum_known"].isoformat().replace("+00:00", "Z"),
                        "observed_at": item["_momentum_observed"].isoformat().replace("+00:00", "Z"),
                    }
                )

            for feature, source_col in (
                ("amihud_20d", "_amihud"),
                ("avg_trading_value_20d", "_avg_value"),
            ):
                value = item[source_col]
                if pd.isna(value):
                    continue
                rows.append(
                    {
                        "date": date,
                        "ticker": ticker,
                        "feature": feature,
                        "value": float(value),
                        "known_at": item["_known"].isoformat().replace("+00:00", "Z"),
                        "observed_at": item["_liq_observed"].isoformat().replace("+00:00", "Z"),
                    }
                )

    result = pd.DataFrame(
        rows,
        columns=["date", "ticker", "feature", "value", "known_at", "observed_at"],
    )
    validate_feature_frame(result)
    return result


def build_forward_returns(
    bars: pd.DataFrame,
    *,
    horizons=(21, 63),
) -> pd.DataFrame:
    """Build realized adjusted-price returns for evaluation only."""
    horizons = tuple(int(h) for h in horizons)
    if not horizons or any(h <= 0 for h in horizons):
        raise ValueError("forward-return horizons must be positive")

    work = _prepare_bars(bars)
    rows = []

    for ticker, group in work.groupby("_ticker", sort=False):
        group = group.copy().reset_index(drop=True)
        current_price = group["_price"]
        for horizon in horizons:
            future_price = current_price.shift(-horizon)
            future_known = group["_known"].shift(-horizon)
            future_observed = group["_observed"].shift(-horizon)
            returns = future_price / current_price - 1

            for idx, value in returns.items():
                if pd.isna(value) or pd.isna(future_known.iloc[idx]):
                    continue
                rows.append(
                    {
                        "date": group.loc[idx, "_date"].date().isoformat(),
                        "ticker": ticker,
                        "horizon_trading_days": horizon,
                        "forward_return": float(value),
                        "decision_known_at": group.loc[idx, "_known"].isoformat().replace("+00:00", "Z"),
                        "outcome_known_at": future_known.iloc[idx].isoformat().replace("+00:00", "Z"),
                        "outcome_observed_at": future_observed.iloc[idx].isoformat().replace("+00:00", "Z"),
                    }
                )

    return pd.DataFrame(
        rows,
        columns=[
            "date",
            "ticker",
            "horizon_trading_days",
            "forward_return",
            "decision_known_at",
            "outcome_known_at",
            "outcome_observed_at",
        ],
    )
