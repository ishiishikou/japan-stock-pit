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
        description=(
            "Signed holding-ratio change for the holder with the largest absolute "
            "reported change in each EDINET document."
        ),
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


def _row_change(item: pd.Series):
    change = _number(item.get("holding_ratio_change"))
    if change is not None:
        return change

    current = _number(item.get("holding_ratio"))
    previous = _number(item.get("previous_holding_ratio"))
    if current is None or previous is None:
        return None
    return current - previous


def build_ownership_features(ownership: pd.DataFrame) -> pd.DataFrame:
    """Build one issuer-level ownership-change feature per EDINET document.

    A normalized ownership document may contain multiple holder rows. To keep
    the common feature contract at one ticker/feature/version row, the holder
    with the largest absolute reported ratio change is selected, while the sign
    of that holder's change is preserved. Missing ratios are never inferred.
    """
    required = {
        "doc_id",
        "issuer_stock_code",
        "known_at",
        "observed_at",
        "holding_ratio",
        "previous_holding_ratio",
    }
    missing = sorted(required - set(ownership.columns))
    if missing:
        raise ValueError(f"missing ownership columns: {missing}")

    rows = []
    for doc_id, frame in ownership.groupby("doc_id", sort=False):
        ticker = next(
            (
                text
                for text in (_text(value) for value in frame["issuer_stock_code"])
                if text is not None
            ),
            None,
        )
        known_at = next(
            (
                text
                for text in (_text(value) for value in frame["known_at"])
                if text is not None
            ),
            None,
        )
        observed_at = next(
            (
                text
                for text in (_text(value) for value in frame["observed_at"])
                if text is not None
            ),
            None,
        )
        filing_date = (
            next(
                (
                    text
                    for text in (
                        _text(value)
                        for value in frame.get("filing_date", pd.Series(dtype=object))
                    )
                    if text is not None
                ),
                None,
            )
            if "filing_date" in frame.columns
            else None
        )
        date = filing_date or (known_at[:10] if known_at else None)
        if not all((ticker, date, known_at, observed_at)):
            continue

        candidates = []
        for _, item in frame.iterrows():
            change = _row_change(item)
            if change is None:
                continue
            candidates.append((abs(change), change, item))

        if not candidates:
            continue

        _, change, selected = max(candidates, key=lambda value: value[0])
        rows.append(
            {
                "date": date,
                "ticker": ticker,
                "feature": "ownership_ratio_change",
                "value": change,
                "known_at": known_at,
                "observed_at": observed_at,
                "source_doc_id": _text(doc_id),
                "holder_name": _text(selected.get("holder_name")),
            }
        )

    result = pd.DataFrame(
        rows,
        columns=[
            "date",
            "ticker",
            "feature",
            "value",
            "known_at",
            "observed_at",
            "source_doc_id",
            "holder_name",
        ],
    )
    validate_feature_frame(result)
    return result
