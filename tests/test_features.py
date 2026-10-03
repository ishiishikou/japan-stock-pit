import pandas as pd
import pytest

from features.contracts import latest_asof, validate_feature_frame


def sample_frame():
    return pd.DataFrame(
        [
            {
                "date": "2026-01-31",
                "ticker": "1234",
                "feature": "roic",
                "value": 0.10,
                "known_at": "2026-01-10T00:00:00Z",
                "observed_at": "2026-01-10T01:00:00Z",
            },
            {
                "date": "2026-01-31",
                "ticker": "1234",
                "feature": "roic",
                "value": 0.12,
                "known_at": "2026-02-10T00:00:00Z",
                "observed_at": "2026-02-10T01:00:00Z",
            },
        ]
    )


def test_latest_asof_blocks_future_revision():
    out = latest_asof(sample_frame(), "2026-01-31T23:59:59Z")
    assert len(out) == 1
    assert out.loc[0, "value"] == 0.10


def test_validate_feature_frame_requires_pit_columns():
    with pytest.raises(ValueError):
        validate_feature_frame(pd.DataFrame({"ticker": ["1234"]}))


def test_validate_feature_frame_rejects_duplicate_versions():
    frame = sample_frame().iloc[[0, 0]].copy()
    with pytest.raises(ValueError):
        validate_feature_frame(frame)
