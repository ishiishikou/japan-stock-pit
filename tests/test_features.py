import pandas as pd
import pytest

from features.contracts import latest_asof, validate_feature_frame
from features.financial import build_financial_features
from features.ownership import build_ownership_features


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
    }
    frame = pd.DataFrame(
        [
            {
                **common,
                "metric": "net_income",
                "numeric_value": 100.0,
                "relative_year": "当期",
                "consolidation": "連結",
                "period_type": "期間",
            },
            {
                **common,
                "metric": "net_income",
                "numeric_value": 999.0,
                "relative_year": "当期",
                "consolidation": "個別",
                "period_type": "期間",
            },
            {
                **common,
                "metric": "net_income",
                "numeric_value": 888.0,
                "relative_year": "前期",
                "consolidation": "連結",
                "period_type": "期間",
            },
            {
                **common,
                "metric": "operating_income",
                "numeric_value": 150.0,
                "relative_year": "当期",
                "consolidation": "連結",
                "period_type": "期間",
            },
            {
                **common,
                "metric": "shareholders_equity",
                "numeric_value": 1000.0,
                "relative_year": "当期末",
                "consolidation": "連結",
                "period_type": "時点",
            },
            {
                **common,
                "metric": "interest_bearing_debt",
                "numeric_value": 500.0,
                "relative_year": "当期末",
                "consolidation": "連結",
                "period_type": "時点",
            },
            {
                **common,
                "metric": "cash_and_deposits",
                "numeric_value": 200.0,
                "relative_year": "当期末",
                "consolidation": "連結",
                "period_type": "時点",
            },
            {
                **common,
                "metric": "operating_cash_flow",
                "numeric_value": 180.0,
                "relative_year": "当期",
                "consolidation": "連結",
                "period_type": "期間",
            },
            {
                **common,
                "metric": "capital_expenditure",
                "numeric_value": 80.0,
                "relative_year": "当期",
                "consolidation": "連結",
                "period_type": "期間",
            },
            {
                **common,
                "metric": "pretax_income",
                "numeric_value": 125.0,
                "relative_year": "当期",
                "consolidation": "連結",
                "period_type": "期間",
            },
            {
                **common,
                "metric": "income_tax",
                "numeric_value": 25.0,
                "relative_year": "当期",
                "consolidation": "連結",
                "period_type": "期間",
            },
        ]
    )

    out = build_financial_features(frame).set_index("feature")

    assert out.loc["roe", "value"] == pytest.approx(0.10)
    assert out.loc["roic_pre_tax_proxy", "value"] == pytest.approx(150.0 / 1300.0)
    assert out.loc["roic", "value"] == pytest.approx((150.0 * 0.8) / 1300.0)
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



def test_build_ownership_features_prefers_reported_change_and_largest_holder_move():
    common = {
        "doc_id": "OWN1",
        "issuer_stock_code": "1234",
        "filing_date": "2026-06-01",
        "known_at": "2026-06-01T01:00:00Z",
        "observed_at": "2026-06-01T02:00:00Z",
    }
    frame = pd.DataFrame(
        [
            {
                **common,
                "holder_name": "Holder A",
                "holding_ratio": 0.08,
                "previous_holding_ratio": 0.05,
                "holding_ratio_change": 0.01,
            },
            {
                **common,
                "holder_name": "Holder B",
                "holding_ratio": 0.12,
                "previous_holding_ratio": 0.07,
                "holding_ratio_change": None,
            },
        ]
    )

    out = build_ownership_features(frame)

    assert len(out) == 1
    assert out.iloc[0]["feature"] == "ownership_ratio_change"
    assert out.iloc[0]["value"] == pytest.approx(0.05)
    assert out.iloc[0]["holder_name"] == "Holder B"


def test_build_ownership_features_does_not_infer_missing_previous_ratio():
    frame = pd.DataFrame(
        [
            {
                "doc_id": "OWN2",
                "issuer_stock_code": "5678",
                "filing_date": "2026-06-02",
                "known_at": "2026-06-02T01:00:00Z",
                "observed_at": "2026-06-02T02:00:00Z",
                "holder_name": "Holder C",
                "holding_ratio": 0.11,
                "previous_holding_ratio": None,
                "holding_ratio_change": None,
            }
        ]
    )

    out = build_ownership_features(frame)

    assert out.empty



def test_build_financial_features_skips_roic_for_invalid_effective_tax_rate():
    common = {
        "doc_id": "DOC3",
        "stock_code": "99990",
        "period_end": "2026-03-31",
        "known_at": "2026-05-10T00:00:00Z",
        "observed_at": "2026-05-10T01:00:00Z",
        "relative_year": "当期",
        "consolidation": "連結",
        "period_type": "期間",
    }
    frame = pd.DataFrame(
        [
            {**common, "metric": "operating_income", "numeric_value": 100.0},
            {**common, "metric": "pretax_income", "numeric_value": 10.0},
            {**common, "metric": "income_tax", "numeric_value": 20.0},
            {
                **common,
                "metric": "shareholders_equity",
                "numeric_value": 500.0,
                "relative_year": "当期末",
                "period_type": "時点",
            },
            {
                **common,
                "metric": "interest_bearing_debt",
                "numeric_value": 100.0,
                "relative_year": "当期末",
                "period_type": "時点",
            },
            {
                **common,
                "metric": "cash_and_deposits",
                "numeric_value": 50.0,
                "relative_year": "当期末",
                "period_type": "時点",
            },
        ]
    )

    out = build_financial_features(frame)

    assert "roic_pre_tax_proxy" in out["feature"].tolist()
    assert "roic" not in out["feature"].tolist()
