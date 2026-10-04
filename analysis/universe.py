"""Point-in-time security universe helpers.

Historical backtests must use the master snapshot for the decision date rather
than today's list of securities. This prevents classic survivorship leakage.
"""

from __future__ import annotations

import pandas as pd

from processors.identifiers import normalize_security_code


UNIVERSE_COLUMNS = [
    "date",
    "ticker",
    "jquants_code",
    "company_name",
    "market_code",
    "market_name",
    "product_category",
    "known_at",
    "observed_at",
]


def _text(value):
    if value is None or pd.isna(value):
        return None
    text = str(value).strip()
    return text or None


def build_universe_snapshot(
    master: pd.DataFrame,
    *,
    allowed_markets=None,
    allowed_product_categories=None,
) -> pd.DataFrame:
    """Normalize one or more dated J-Quants master snapshots.

    The function deliberately makes no implicit market/product assumptions.
    Optional filters are explicit and therefore reproducible.
    """
    required = {"Date", "Code", "known_at", "observed_at"}
    missing = sorted(required - set(master.columns))
    if missing:
        raise ValueError(f"missing J-Quants master columns: {missing}")

    allowed_markets = (
        {str(x) for x in allowed_markets}
        if allowed_markets is not None
        else None
    )
    allowed_products = (
        {str(x) for x in allowed_product_categories}
        if allowed_product_categories is not None
        else None
    )

    rows = []
    for _, item in master.iterrows():
        ticker = normalize_security_code(item.get("Code"))
        date = _text(item.get("Date"))
        known = pd.to_datetime(item.get("known_at"), utc=True, errors="coerce")
        observed = pd.to_datetime(item.get("observed_at"), utc=True, errors="coerce")
        if ticker is None or date is None or pd.isna(known) or pd.isna(observed):
            continue

        market = _text(item.get("Mkt"))
        product = _text(item.get("ProdCat"))
        if allowed_markets is not None and market not in allowed_markets:
            continue
        if allowed_products is not None and product not in allowed_products:
            continue

        rows.append(
            {
                "date": date[:10],
                "ticker": ticker,
                "jquants_code": _text(item.get("Code")),
                "company_name": _text(item.get("CoName")),
                "market_code": market,
                "market_name": _text(item.get("MktNm")),
                "product_category": product,
                "known_at": known.isoformat().replace("+00:00", "Z"),
                "observed_at": observed.isoformat().replace("+00:00", "Z"),
            }
        )

    out = pd.DataFrame(rows, columns=UNIVERSE_COLUMNS)
    if out.empty:
        return out

    # One security should appear at most once per dated master snapshot.
    out = out.sort_values(
        ["date", "ticker", "known_at", "observed_at"],
        kind="stable",
    )
    out = out.drop_duplicates(["date", "ticker"], keep="last")
    return out.reset_index(drop=True)


def filter_to_point_in_time_universe(
    frame: pd.DataFrame,
    universe: pd.DataFrame,
    *,
    date_col="date",
    ticker_col="ticker",
) -> pd.DataFrame:
    """Keep rows whose security existed in the universe snapshot on that date.

    Matching is intentionally exact on (date, ticker). No latest-current-master
    fallback is allowed because that would reintroduce survivorship bias.
    """
    for col in (date_col, ticker_col):
        if col not in frame.columns:
            raise KeyError(f"missing frame column: {col}")
    for col in ("date", "ticker"):
        if col not in universe.columns:
            raise KeyError(f"missing universe column: {col}")

    work = frame.copy()
    work["_universe_date"] = work[date_col].astype(str).str[:10]
    work["_universe_ticker"] = work[ticker_col].map(normalize_security_code)

    membership = universe[["date", "ticker"]].drop_duplicates().rename(
        columns={
            "date": "_universe_date",
            "ticker": "_universe_ticker",
        }
    )
    out = work.merge(
        membership,
        on=["_universe_date", "_universe_ticker"],
        how="inner",
    )
    return out.drop(
        columns=["_universe_date", "_universe_ticker"]
    ).reset_index(drop=True)
