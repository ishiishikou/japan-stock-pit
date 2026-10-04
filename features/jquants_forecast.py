"""Point-in-time management forecast revision features from J-Quants financial summary."""

from __future__ import annotations

import math

import pandas as pd

from features.contracts import FeatureDefinition, validate_feature_frame


FORECAST_COLUMNS = {
    "FSales": "forecast_sales_revision_pct",
    "FOP": "forecast_operating_profit_revision_pct",
    "FOdP": "forecast_ordinary_profit_revision_pct",
    "FNP": "forecast_net_profit_revision_pct",
    "FEPS": "forecast_eps_revision_pct",
}

FEATURE_DEFINITIONS = {
    name: FeatureDefinition(
        name=name,
        higher_is_better=True,
        source_datasets=("financial_summary",),
        description=(
            "Change from the previous disclosed management forecast for the same "
            "company and fiscal-year end, divided by the absolute previous forecast."
        ),
    )
    for name in FORECAST_COLUMNS.values()
}
FEATURE_DEFINITIONS["forecast_revision_breadth"] = FeatureDefinition(
    name="forecast_revision_breadth",
    higher_is_better=True,
    source_datasets=("financial_summary",),
    description=(
        "Mean sign of available management-forecast changes across sales, operating "
        "profit, ordinary profit, net profit and EPS; ranges from -1 to +1."
    ),
)


def normalize_jquants_ticker(value):
    if value is None or pd.isna(value):
        return None
    text = str(value).strip()
    if text.endswith(".0"):
        text = text[:-2]
    if len(text) == 5 and text.isdigit() and text.endswith("0"):
        return text[:4]
    return text or None


def _number(value):
    number = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
    if pd.isna(number):
        return None
    number = float(number)
    return number if math.isfinite(number) else None


def _text(value):
    if value is None or pd.isna(value):
        return None
    value = str(value).strip()
    return value or None


def _revision_ratio(current, previous):
    if current is None or previous is None or previous == 0:
        return None
    return (current - previous) / abs(previous)


def build_forecast_revision_features(summary: pd.DataFrame) -> pd.DataFrame:
    """Build revision features from successive J-Quants financial-summary disclosures.

    Rows are compared only within the same Code and CurFYEn. A row must have a
    prior disclosure for that same fiscal-year end. Missing forecasts are not
    filled across metrics, and percentage revisions are omitted when the prior
    value is zero. Breadth can still represent zero-crossing revisions.
    """
    required = {
        "Code",
        "DiscDate",
        "CurFYEn",
        "known_at",
        "observed_at",
        *FORECAST_COLUMNS.keys(),
    }
    missing = sorted(required - set(summary.columns))
    if missing:
        raise ValueError(f"missing J-Quants financial-summary columns: {missing}")

    work = summary.copy()
    work["_ticker"] = work["Code"].map(normalize_jquants_ticker)
    work["_known"] = pd.to_datetime(work["known_at"], utc=True, errors="coerce")
    work["_observed"] = pd.to_datetime(work["observed_at"], utc=True, errors="coerce")
    work["_fy_end"] = work["CurFYEn"].map(_text)
    work["_disc_date"] = work["DiscDate"].map(_text)

    work = work.loc[
        work["_ticker"].notna()
        & work["_fy_end"].notna()
        & work["_disc_date"].notna()
        & work["_known"].notna()
        & work["_observed"].notna()
    ].copy()
    work = work.sort_values(
        ["_ticker", "_fy_end", "_known", "_observed"],
        kind="stable",
    )

    rows = []
    for (_, fy_end), group in work.groupby(["_ticker", "_fy_end"], sort=False):
        previous_values = None
        for _, item in group.iterrows():
            current_values = {
                col: _number(item.get(col))
                for col in FORECAST_COLUMNS
            }

            if previous_values is not None:
                changes = []
                for col, feature_name in FORECAST_COLUMNS.items():
                    current = current_values[col]
                    previous = previous_values.get(col)
                    if current is None or previous is None:
                        continue

                    delta = current - previous
                    changes.append(1.0 if delta > 0 else (-1.0 if delta < 0 else 0.0))

                    ratio = _revision_ratio(current, previous)
                    if ratio is not None:
                        rows.append(
                            {
                                "date": item["_disc_date"],
                                "ticker": item["_ticker"],
                                "feature": feature_name,
                                "value": ratio,
                                "known_at": item["known_at"],
                                "observed_at": item["observed_at"],
                                "source_fiscal_year_end": fy_end,
                                "source_disc_no": _text(item.get("DiscNo")),
                            }
                        )

                if changes:
                    rows.append(
                        {
                            "date": item["_disc_date"],
                            "ticker": item["_ticker"],
                            "feature": "forecast_revision_breadth",
                            "value": sum(changes) / len(changes),
                            "known_at": item["known_at"],
                            "observed_at": item["observed_at"],
                            "source_fiscal_year_end": fy_end,
                            "source_disc_no": _text(item.get("DiscNo")),
                        }
                    )

            # Keep the latest explicitly disclosed value per metric. If a disclosure
            # omits one forecast field, retain the previous explicit value so the next
            # actual revision can still be compared with the last known forecast.
            if previous_values is None:
                previous_values = {}
            for col, value in current_values.items():
                if value is not None:
                    previous_values[col] = value

    result = pd.DataFrame(
        rows,
        columns=[
            "date",
            "ticker",
            "feature",
            "value",
            "known_at",
            "observed_at",
            "source_fiscal_year_end",
            "source_disc_no",
        ],
    )
    validate_feature_frame(result)
    return result



def build_forecast_revision_features_with_state(summary: pd.DataFrame, state=None):
    """Build daily revision features and return an updated serializable state.

    State keys are "<ticker>|<CurFYEn>" and store only the latest explicitly
    disclosed forecast values. Missing fields never erase prior forecasts.
    """
    required = {
        "Code",
        "DiscDate",
        "CurFYEn",
        "known_at",
        "observed_at",
        *FORECAST_COLUMNS.keys(),
    }
    missing = sorted(required - set(summary.columns))
    if missing:
        raise ValueError(f"missing J-Quants financial-summary columns: {missing}")

    state = {
        str(key): dict(value)
        for key, value in (state or {}).items()
        if isinstance(value, dict)
    }

    work = summary.copy()
    work["_ticker"] = work["Code"].map(normalize_jquants_ticker)
    work["_known"] = pd.to_datetime(work["known_at"], utc=True, errors="coerce")
    work["_observed"] = pd.to_datetime(work["observed_at"], utc=True, errors="coerce")
    work["_fy_end"] = work["CurFYEn"].map(_text)
    work["_disc_date"] = work["DiscDate"].map(_text)
    work = work.loc[
        work["_ticker"].notna()
        & work["_fy_end"].notna()
        & work["_disc_date"].notna()
        & work["_known"].notna()
        & work["_observed"].notna()
    ].copy()
    work = work.sort_values(
        ["_ticker", "_fy_end", "_known", "_observed"],
        kind="stable",
    )

    rows = []
    for _, item in work.iterrows():
        ticker = item["_ticker"]
        fy_end = item["_fy_end"]
        state_key = f"{ticker}|{fy_end}"
        previous = dict(state.get(state_key) or {})
        current = {col: _number(item.get(col)) for col in FORECAST_COLUMNS}

        if previous:
            changes = []
            for col, feature_name in FORECAST_COLUMNS.items():
                current_value = current[col]
                previous_value = _number(previous.get(col))
                if current_value is None or previous_value is None:
                    continue

                delta = current_value - previous_value
                changes.append(
                    1.0 if delta > 0 else (-1.0 if delta < 0 else 0.0)
                )
                ratio = _revision_ratio(current_value, previous_value)
                if ratio is not None:
                    rows.append(
                        {
                            "date": item["_disc_date"],
                            "ticker": ticker,
                            "feature": feature_name,
                            "value": ratio,
                            "known_at": item["known_at"],
                            "observed_at": item["observed_at"],
                            "source_fiscal_year_end": fy_end,
                            "source_disc_no": _text(item.get("DiscNo")),
                        }
                    )

            if changes:
                rows.append(
                    {
                        "date": item["_disc_date"],
                        "ticker": ticker,
                        "feature": "forecast_revision_breadth",
                        "value": sum(changes) / len(changes),
                        "known_at": item["known_at"],
                        "observed_at": item["observed_at"],
                        "source_fiscal_year_end": fy_end,
                        "source_disc_no": _text(item.get("DiscNo")),
                    }
                )

        updated = previous
        for col, value in current.items():
            if value is not None:
                updated[col] = value
        updated["_last_known_at"] = str(item["known_at"])
        updated["_last_disc_no"] = _text(item.get("DiscNo"))
        state[state_key] = updated

    result = pd.DataFrame(
        rows,
        columns=[
            "date",
            "ticker",
            "feature",
            "value",
            "known_at",
            "observed_at",
            "source_fiscal_year_end",
            "source_disc_no",
        ],
    )
    validate_feature_frame(result)
    return result, state
