"""Cross-sectional factor neutralization and portfolio cost helpers."""

from __future__ import annotations

import numpy as np
import pandas as pd


def neutralize_factor(
    frame: pd.DataFrame,
    *,
    date_col: str = "date",
    factor_col: str = "factor",
    sector_col: str = "sector",
    size_col: str = "market_cap",
    output_col: str = "neutralized_factor",
    min_observations: int = 10,
) -> pd.DataFrame:
    """Residualize a factor against log size and sector fixed effects by date.

    Rows with missing factor/sector or nonpositive size remain unscored. The
    residual is standardized to unit population standard deviation per date.
    """
    required = {date_col, factor_col, sector_col, size_col}
    missing = sorted(required - set(frame.columns))
    if missing:
        raise KeyError(f"missing neutralization columns: {missing}")
    if min_observations < 3:
        raise ValueError("min_observations must be >= 3")

    out = frame.copy()
    out[output_col] = np.nan

    for _, idx in out.groupby(date_col, sort=False).groups.items():
        group = out.loc[idx].copy()
        factor = pd.to_numeric(group[factor_col], errors="coerce")
        size = pd.to_numeric(group[size_col], errors="coerce")
        sector = group[sector_col].astype("string")
        eligible = (
            factor.notna()
            & size.notna()
            & (size > 0)
            & sector.notna()
        )
        if int(eligible.sum()) < min_observations:
            continue

        y = factor.loc[eligible].astype(float).to_numpy()
        log_size = np.log(size.loc[eligible].astype(float).to_numpy())
        sector_dummies = pd.get_dummies(
            sector.loc[eligible],
            prefix="sector",
            drop_first=True,
            dtype=float,
        )

        columns = [np.ones(len(y)), log_size]
        if not sector_dummies.empty:
            columns.extend(
                sector_dummies[col].to_numpy(dtype=float)
                for col in sector_dummies.columns
            )
        x = np.column_stack(columns)

        # Require at least one residual degree of freedom beyond the regressors.
        if len(y) <= x.shape[1]:
            continue

        beta, *_ = np.linalg.lstsq(x, y, rcond=None)
        residual = y - x @ beta
        scale = residual.std(ddof=0)
        if not np.isfinite(scale) or scale == 0:
            continue

        standardized = residual / scale
        out.loc[factor.loc[eligible].index, output_col] = standardized

    return out


def portfolio_turnover(
    weights: pd.DataFrame,
    *,
    date_col: str = "date",
    ticker_col: str = "ticker",
    weight_col: str = "weight",
    include_initial: bool = False,
) -> pd.DataFrame:
    """Compute one-way turnover = 0.5 * sum(abs(w_t - w_t-1)).

    Securities absent on either date are treated as zero weight. The 0.5
    convention avoids double-counting matched buys and sells.
    """
    required = {date_col, ticker_col, weight_col}
    missing = sorted(required - set(weights.columns))
    if missing:
        raise KeyError(f"missing weight columns: {missing}")

    work = weights.copy()
    work[weight_col] = pd.to_numeric(work[weight_col], errors="coerce")
    if work[weight_col].isna().any():
        raise ValueError("weights contain non-numeric values")
    if work.duplicated([date_col, ticker_col]).any():
        raise ValueError("duplicate date/ticker weights")

    dates = sorted(work[date_col].astype(str).unique())
    previous = {}
    rows = []
    for i, date in enumerate(dates):
        current_rows = work.loc[work[date_col].astype(str) == date]
        current = dict(
            zip(
                current_rows[ticker_col].astype(str),
                current_rows[weight_col].astype(float),
            )
        )

        if i == 0 and not include_initial:
            turnover = np.nan
        else:
            tickers = set(previous) | set(current)
            turnover = 0.5 * sum(
                abs(current.get(ticker, 0.0) - previous.get(ticker, 0.0))
                for ticker in tickers
            )
        rows.append({date_col: date, "turnover": turnover})
        previous = current

    return pd.DataFrame(rows)


def apply_transaction_costs(
    returns: pd.DataFrame,
    turnover: pd.DataFrame,
    *,
    date_col: str = "date",
    return_col: str = "gross_return",
    cost_bps: float = 10.0,
) -> pd.DataFrame:
    """Subtract a simple linear transaction-cost estimate from gross returns."""
    if cost_bps < 0:
        raise ValueError("cost_bps must be nonnegative")
    if date_col not in returns.columns or return_col not in returns.columns:
        raise KeyError("returns missing date or gross-return column")
    if date_col not in turnover.columns or "turnover" not in turnover.columns:
        raise KeyError("turnover frame missing required columns")

    out = returns.merge(turnover[[date_col, "turnover"]], on=date_col, how="left")
    out[return_col] = pd.to_numeric(out[return_col], errors="coerce")
    out["turnover"] = pd.to_numeric(out["turnover"], errors="coerce")
    out["transaction_cost"] = out["turnover"] * (float(cost_bps) / 10_000.0)
    out["net_return"] = out[return_col] - out["transaction_cost"]
    return out
