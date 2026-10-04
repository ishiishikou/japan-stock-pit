import pandas as pd
import pytest

from features.contracts import latest_asof, validate_feature_frame
from features.financial import build_financial_features


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



def test_build_financial_features_uses_current_consolidated_values():
    common = {
        "doc_id": "DOC1",
        "stock_code": "12340",
        "period_end": "2026-03-31",
        "known_at": "2026-05-10T00:00:00Z",
        "observed_at": "2026-05-10T01:00:00Z",
        "relative_year": "CurrentYear",
        "consolidation": "Consolidated",
    }
    frame = pd.DataFrame(
        [
            {**common, "metric": "net_income", "numeric_value": 100.0, "period_type": "duration"},
            {**common, "metric": "operating_income", "numeric_value": 150.0, "period_type": "duration"},
            {**common, "metric": "shareholders_equity", "numeric_value": 1000.0, "period_type": "instant"},
            {**common, "metric": "interest_bearing_debt", "numeric_value": 500.0, "period_type": "instant"},
            {**common, "metric": "cash_and_deposits", "numeric_value": 200.0, "period_type": "instant"},
            {**common, "metric": "operating_cash_flow", "numeric_value": 180.0, "period_type": "duration"},
            {**common, "metric": "capital_expenditure", "numeric_value": 80.0, "period_type": "duration"},
            {
                **common,
                "metric": "net_income",
                "numeric_value": 999.0,
                "period_type": "duration",
                "relative_year": "Prior1Year",
            },
        ]
    )

    out = build_financial_features(frame).set_index("feature")

    assert out.loc["roe", "value"] == pytest.approx(0.10)
    assert out.loc["roic_pre_tax_proxy", "value"] == pytest.approx(150.0 / 1300.0)
    assert out.loc["fcf_conversion", "value"] == pytest.approx(1.0)
    assert out.loc["net_debt", "value"] == pytest.approx(300.0)


def test_build_financial_features_does_not_infer_missing_metrics():
    frame = pd.DataFrame(
        [
            {
                "doc_id": "DOC2",
                "stock_code": "56780",
                "period_end": "2026-03-31",
                "known_at": "2026-05-10T00:00:00Z",
                "observed_at": "2026-05-10T01:00:00Z",
                "metric": "net_income",
                "numeric_value": 100.0,
                "relative_year": "CurrentYear",
                "consolidation": "Consolidated",
                "period_type": "duration",
            },
            {
                "doc_id": "DOC2",
                "stock_code": "56780",
                "period_end": "2026-03-31",
                "known_at": "2026-05-10T00:00:00Z",
                "observed_at": "2026-05-10T01:00:00Z",
                "metric": "net_assets",
                "numeric_value": 800.0,
                "relative_year": "CurrentYear",
                "consolidation": "Consolidated",
                "period_type": "instant",
            },
        ]
    )

    out = build_financial_features(frame)

    assert out["feature"].tolist() == ["roe"]
    assert out.iloc[0]["value"] == pytest.approx(0.125)
