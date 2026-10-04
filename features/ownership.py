"""PIT ownership features from normalized EDINET large-shareholding reports."""

from __future__ import annotations

import math
import pandas as pd
from features.contracts import FeatureDefinition, validate_feature_frame

FEATURE_DEFINITIONS = {
    "ownership_ratio_change": FeatureDefinition(
        name="ownership_ratio_change",
        higher_is_better=True,
        source_datasets=("ownership_summary",),
        description="Current large-holder ownership ratio minus the previous reported ratio.",
    ),
}

def _text(value):
    if value is None or pd.isna(value):
        return None
    value = str(value).strip()
    return value or None

def _number(value):
    value = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
    if pd.isna(value):
        return None
    value = float(value)
    return value if math.isfinite(value) else None

def build_ownership_features(ownership: pd.DataFrame) -> pd.DataFrame:
    """Build one ownership-change feature per normalized holder row."""
    required = {"doc_id", "issuer_stock_code", "known_at", "observed_at",
                "holding_ratio", "previous_holding_ratio"}
    missing = sorted(required - set(ownership.columns))
    if missing:
        raise ValueError(f"missing ownership columns: {missing}")

    rows = []
    for _, item in ownership.iterrows():
        ticker = _text(item.get("issuer_stock_code"))
        known_at = _text(item.get("known_at"))
        observed_at = _text(item.get("observed_at"))
        date = _text(item.get("filing_date")) or (known_at[:10] if known_at else None)
        if not all((ticker, date, known_at, observed_at)):
            continue

        change = _number(item.get("holding_ratio_change"))
        if change is None:
            current = _number(item.get("holding_ratio"))
            previous = _number(item.get("previous_holding_ratio"))
            if current is None or previous is None:
                continue
            change = current - previous

        rows.append({
            "date": date, "ticker": ticker, "feature": "ownership_ratio_change",
            "value": change, "known_at": known_at, "observed_at": observed_at,
            "source_doc_id": _text(item.get("doc_id")),
            "holder_name": _text(item.get("holder_name")),
        })

    result = pd.DataFrame(rows, columns=[
        "date", "ticker", "feature", "value", "known_at", "observed_at",
        "source_doc_id", "holder_name",
    ])
    validate_feature_frame(result)
    return result
