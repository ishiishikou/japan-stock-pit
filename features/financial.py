"""Derived PIT financial features from canonical EDINET metrics."""

from __future__ import annotations

import math

import pandas as pd

from features.contracts import FeatureDefinition, validate_feature_frame


FEATURE_DEFINITIONS = {
    "roe": FeatureDefinition(
        name="roe",
        higher_is_better=True,
        source_datasets=("financial_metrics",),
        description=(
            "Net income divided by period-end shareholders' equity, "
            "falling back to net assets when shareholders' equity is unavailable."
        ),
    ),
    "roic_pre_tax_proxy": FeatureDefinition(
        name="roic_pre_tax_proxy",
        higher_is_better=True,
        source_datasets=("financial_metrics",),
        description=(
            "Operating income divided by period-end invested capital proxy "
            "(equity + interest-bearing debt - cash). No tax adjustment is applied."
        ),
    ),
    "fcf_conversion": FeatureDefinition(
        name="fcf_conversion",
        higher_is_better=True,
        source_datasets=("financial_metrics",),
        description=(
            "(Operating cash flow - absolute capital expenditure) divided by net income."
        ),
    ),
    "net_debt": FeatureDefinition(
        name="net_debt",
        higher_is_better=False,
        source_datasets=("financial_metrics",),
        description="Interest-bearing debt minus cash and deposits.",
    ),
}


_BALANCE_METRICS = {
    "shareholders_equity",
    "net_assets",
    "interest_bearing_debt",
    "cash_and_deposits",
}
_FLOW_METRICS = {
    "net_income",
    "operating_income",
    "operating_cash_flow",
    "capital_expenditure",
}


def _clean_text(value):
    if value is None or pd.isna(value):
        return None
    text = str(value).strip()
    return text or None


def _is_current_year(value) -> bool:
    text = (_clean_text(value) or "").lower()
    return text in {"currentyear", "current_year", "current", "0"}


def _is_consolidated(value) -> bool:
    text = (_clean_text(value) or "").lower()
    return "consolidated" in text and "nonconsolidated" not in text


def _row_score(row: pd.Series, metric: str) -> tuple[int, int, int]:
    relative = 0 if _is_current_year(row.get("relative_year")) else 1
    consolidation_text = _clean_text(row.get("consolidation"))
    consolidation = 0 if _is_consolidated(consolidation_text) else (1 if consolidation_text is None else 2)

    period = (_clean_text(row.get("period_type")) or "").lower()
    if metric in _BALANCE_METRICS:
        period_score = 0 if period == "instant" else (1 if not period else 2)
    elif metric in _FLOW_METRICS:
        period_score = 0 if period == "duration" else (1 if not period else 2)
    else:
        period_score = 1
    return relative, consolidation, period_score


def _pick_metric(frame: pd.DataFrame, metric: str):
    rows = frame.loc[
        (frame["metric"] == metric) & pd.to_numeric(frame["numeric_value"], errors="coerce").notna()
    ].copy()
    if rows.empty:
        return None

    rows["_score"] = rows.apply(lambda row: _row_score(row, metric), axis=1)
    rows = rows.sort_values("_score", kind="stable")
    return float(rows.iloc[0]["numeric_value"])


def _safe_ratio(numerator, denominator):
    if numerator is None or denominator is None:
        return None
    if not math.isfinite(numerator) or not math.isfinite(denominator) or denominator == 0:
        return None
    return numerator / denominator


def _first_text(frame: pd.DataFrame, column: str):
    if column not in frame.columns:
        return None
    for value in frame[column]:
        text = _clean_text(value)
        if text is not None:
            return text
    return None


def build_financial_features(metrics: pd.DataFrame) -> pd.DataFrame:
    """Build PIT feature rows per EDINET document.

    The function intentionally emits no feature when required canonical metrics
    are unavailable. It does not infer missing debt, cash, capex, or tax values.
    """
    required = {
        "doc_id",
        "stock_code",
        "period_end",
        "known_at",
        "observed_at",
        "metric",
        "numeric_value",
    }
    missing = sorted(required - set(metrics.columns))
    if missing:
        raise ValueError(f"missing financial metric columns: {missing}")

    out = []
    for doc_id, frame in metrics.groupby("doc_id", sort=False):
        ticker = _first_text(frame, "stock_code")
        date = _first_text(frame, "period_end")
        known_at = _first_text(frame, "known_at")
        observed_at = _first_text(frame, "observed_at")
        if not all((ticker, date, known_at, observed_at)):
            continue

        values = {
            metric: _pick_metric(frame, metric)
            for metric in (
                "net_income",
                "operating_income",
                "shareholders_equity",
                "net_assets",
                "interest_bearing_debt",
                "cash_and_deposits",
                "operating_cash_flow",
                "capital_expenditure",
            )
        }

        equity = (
            values["shareholders_equity"]
            if values["shareholders_equity"] is not None
            else values["net_assets"]
        )

        features = {}

        roe = _safe_ratio(values["net_income"], equity)
        if roe is not None:
            features["roe"] = roe

        if (
            equity is not None
            and values["interest_bearing_debt"] is not None
            and values["cash_and_deposits"] is not None
        ):
            invested_capital = (
                equity
                + values["interest_bearing_debt"]
                - values["cash_and_deposits"]
            )
            roic = _safe_ratio(values["operating_income"], invested_capital)
            if roic is not None:
                features["roic_pre_tax_proxy"] = roic

        if (
            values["operating_cash_flow"] is not None
            and values["capital_expenditure"] is not None
        ):
            free_cash_flow = (
                values["operating_cash_flow"]
                - abs(values["capital_expenditure"])
            )
            conversion = _safe_ratio(free_cash_flow, values["net_income"])
            if conversion is not None:
                features["fcf_conversion"] = conversion

        if (
            values["interest_bearing_debt"] is not None
            and values["cash_and_deposits"] is not None
        ):
            features["net_debt"] = (
                values["interest_bearing_debt"] - values["cash_and_deposits"]
            )

        for feature, value in features.items():
            out.append(
                {
                    "date": date,
                    "ticker": ticker,
                    "feature": feature,
                    "value": value,
                    "known_at": known_at,
                    "observed_at": observed_at,
                    "source_doc_id": doc_id,
                }
            )

    result = pd.DataFrame(
        out,
        columns=[
            "date",
            "ticker",
            "feature",
            "value",
            "known_at",
            "observed_at",
            "source_doc_id",
        ],
    )
    validate_feature_frame(result)
    return result
