"""Common contract for derived point-in-time features."""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


REQUIRED_FEATURE_COLUMNS = (
    "date",
    "ticker",
    "feature",
    "value",
    "known_at",
    "observed_at",
)


@dataclass(frozen=True)
class FeatureDefinition:
    name: str
    higher_is_better: bool
    source_datasets: tuple[str, ...]
    description: str = ""


def validate_feature_frame(frame: pd.DataFrame) -> None:
    """Validate the minimum schema expected from every feature generator."""
    missing = [c for c in REQUIRED_FEATURE_COLUMNS if c not in frame.columns]
    if missing:
        raise ValueError(f"missing required feature columns: {missing}")

    if frame.empty:
        return

    known = pd.to_datetime(frame["known_at"], utc=True, errors="coerce")
    observed = pd.to_datetime(frame["observed_at"], utc=True, errors="coerce")
    if known.isna().any():
        raise ValueError("feature frame contains invalid known_at")
    if observed.isna().any():
        raise ValueError("feature frame contains invalid observed_at")

    duplicate_key = ["date", "ticker", "feature", "known_at", "observed_at"]
    if frame.duplicated(duplicate_key).any():
        raise ValueError("feature frame contains duplicate PIT feature versions")


def latest_asof(
    frame: pd.DataFrame,
    decision_time,
    *,
    logical_key: tuple[str, ...] = ("ticker", "feature"),
) -> pd.DataFrame:
    """Select the latest feature version provably known by decision_time."""
    validate_feature_frame(frame)
    if frame.empty:
        return frame.copy()

    missing = [c for c in logical_key if c not in frame.columns]
    if missing:
        raise KeyError(f"missing logical-key columns: {missing}")

    decision = pd.Timestamp(decision_time)
    if decision.tzinfo is None:
        decision = decision.tz_localize("UTC")
    else:
        decision = decision.tz_convert("UTC")

    work = frame.copy()
    work["_known"] = pd.to_datetime(work["known_at"], utc=True)
    work["_observed"] = pd.to_datetime(work["observed_at"], utc=True)
    work = work.loc[work["_known"] <= decision]
    if work.empty:
        return frame.iloc[0:0].copy()

    work = work.sort_values(
        [*logical_key, "_known", "_observed"],
        ascending=[True] * len(logical_key) + [True, True],
    )
    work = work.groupby(list(logical_key), as_index=False, sort=False).tail(1)
    return work.drop(columns=["_known", "_observed"]).reset_index(drop=True)
