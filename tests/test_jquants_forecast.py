import pandas as pd
import pytest

from features.jquants_forecast import (
    build_forecast_revision_features,
    build_forecast_revision_features_with_state,
    normalize_jquants_ticker,
)


def test_normalize_jquants_ticker_drops_check_digit_zero():
    assert normalize_jquants_ticker("72030") == "7203"
    assert normalize_jquants_ticker(72030.0) == "7203"
    assert normalize_jquants_ticker("130A0") == "130A"


def test_build_forecast_revision_features_compares_same_fiscal_year_only():
    common = {
        "Code": "72030",
        "CurFYEn": "2027-03-31",
        "observed_at": "2026-10-01T06:05:00Z",
        "FOdP": 90.0,
        "FEPS": 20.0,
    }
    frame = pd.DataFrame(
        [
            {
                **common,
                "DiscDate": "2026-05-10",
                "DiscNo": "A",
                "known_at": "2026-05-10T06:00:00Z",
                "FSales": 1000.0,
                "FOP": 100.0,
                "FNP": 70.0,
            },
            {
                **common,
                "DiscDate": "2026-08-10",
                "DiscNo": "B",
                "known_at": "2026-08-10T06:00:00Z",
                "FSales": 1100.0,
                "FOP": 80.0,
                "FNP": 77.0,
                "FOdP": 90.0,
                "FEPS": 20.0,
            },
            {
                **common,
                "CurFYEn": "2028-03-31",
                "DiscDate": "2026-08-10",
                "DiscNo": "C",
                "known_at": "2026-08-10T06:00:00Z",
                "FSales": 2000.0,
                "FOP": 200.0,
                "FNP": 140.0,
            },
        ]
    )

    out = build_forecast_revision_features(frame).set_index("feature")

    assert out.loc["forecast_sales_revision_pct", "value"] == pytest.approx(0.10)
    assert out.loc["forecast_operating_profit_revision_pct", "value"] == pytest.approx(-0.20)
    assert out.loc["forecast_net_profit_revision_pct", "value"] == pytest.approx(0.10)
    assert out.loc["forecast_ordinary_profit_revision_pct", "value"] == pytest.approx(0.0)
    assert out.loc["forecast_eps_revision_pct", "value"] == pytest.approx(0.0)
    assert out.loc["forecast_revision_breadth", "value"] == pytest.approx(0.2)
    assert out.loc["forecast_revision_breadth", "ticker"] == "7203"


def test_forecast_revision_breadth_handles_zero_crossing_without_pct():
    frame = pd.DataFrame(
        [
            {
                "Code": "12340",
                "DiscDate": "2026-05-01",
                "DiscNo": "A",
                "CurFYEn": "2027-03-31",
                "known_at": "2026-05-01T06:00:00Z",
                "observed_at": "2026-08-01T00:00:00Z",
                "FSales": 100.0,
                "FOP": 0.0,
                "FOdP": None,
                "FNP": None,
                "FEPS": None,
            },
            {
                "Code": "12340",
                "DiscDate": "2026-08-01",
                "DiscNo": "B",
                "CurFYEn": "2027-03-31",
                "known_at": "2026-08-01T06:00:00Z",
                "observed_at": "2026-10-24T00:00:00Z",
                "FSales": 100.0,
                "FOP": 20.0,
                "FOdP": None,
                "FNP": None,
                "FEPS": None,
            },
        ]
    )

    out = build_forecast_revision_features(frame)

    assert "forecast_operating_profit_revision_pct" not in out["feature"].tolist()
    breadth = out.loc[out["feature"] == "forecast_revision_breadth", "value"].iloc[0]
    assert breadth == pytest.approx(0.5)


def test_forecast_revision_retains_last_explicit_forecast_when_field_is_omitted():
    frame = pd.DataFrame(
        [
            {
                "Code": "99990",
                "DiscDate": "2026-05-01",
                "DiscNo": "A",
                "CurFYEn": "2027-03-31",
                "known_at": "2026-05-01T06:00:00Z",
                "observed_at": "2026-08-01T00:00:00Z",
                "FSales": 100.0,
                "FOP": 10.0,
                "FOdP": None,
                "FNP": None,
                "FEPS": None,
            },
            {
                "Code": "99990",
                "DiscDate": "2026-06-01",
                "DiscNo": "B",
                "CurFYEn": "2027-03-31",
                "known_at": "2026-06-01T06:00:00Z",
                "observed_at": "2026-08-24T00:00:00Z",
                "FSales": 100.0,
                "FOP": None,
                "FOdP": None,
                "FNP": None,
                "FEPS": None,
            },
            {
                "Code": "99990",
                "DiscDate": "2026-08-01",
                "DiscNo": "C",
                "CurFYEn": "2027-03-31",
                "known_at": "2026-08-01T06:00:00Z",
                "observed_at": "2026-10-24T00:00:00Z",
                "FSales": 100.0,
                "FOP": 12.0,
                "FOdP": None,
                "FNP": None,
                "FEPS": None,
            },
        ]
    )

    out = build_forecast_revision_features(frame)
    op = out.loc[out["feature"] == "forecast_operating_profit_revision_pct"]

    assert len(op) == 1
    assert op.iloc[0]["value"] == pytest.approx(0.2)



def test_stateful_forecast_revision_is_idempotent():
    first = pd.DataFrame(
        [
            {
                "Code": "72030",
                "DiscDate": "2026-05-10",
                "DiscNo": "A",
                "CurFYEn": "2027-03-31",
                "known_at": "2026-05-10T06:00:00Z",
                "observed_at": "2026-08-02T00:00:00Z",
                "FSales": 1000.0,
                "FOP": 100.0,
                "FOdP": 90.0,
                "FNP": 70.0,
                "FEPS": 20.0,
            }
        ]
    )
    first_features, state = build_forecast_revision_features_with_state(first, {})

    assert first_features.empty
    assert state["7203|2027-03-31"]["FOP"] == pytest.approx(100.0)

    second = first.copy()
    second.loc[0, "DiscDate"] = "2026-08-10"
    second.loc[0, "DiscNo"] = "B"
    second.loc[0, "known_at"] = "2026-08-10T06:00:00Z"
    second.loc[0, "observed_at"] = "2026-11-02T00:00:00Z"
    second.loc[0, "FOP"] = 120.0

    features, state = build_forecast_revision_features_with_state(second, state)
    assert (
        features.loc[
            features["feature"] == "forecast_operating_profit_revision_pct",
            "value",
        ].iloc[0]
        == pytest.approx(0.2)
    )

    repeated, repeated_state = build_forecast_revision_features_with_state(
        second, state
    )
    assert repeated.empty
    assert repeated_state == state


def test_stateful_forecast_revision_does_not_roll_state_back():
    newer_state = {
        "7203|2027-03-31": {
            "FSales": 1100.0,
            "FOP": 120.0,
            "_last_known_at": "2026-08-10T06:00:00Z",
            "_last_disc_no": "B",
        }
    }
    older = pd.DataFrame(
        [
            {
                "Code": "72030",
                "DiscDate": "2026-05-10",
                "DiscNo": "A",
                "CurFYEn": "2027-03-31",
                "known_at": "2026-05-10T06:00:00Z",
                "observed_at": "2026-11-02T00:00:00Z",
                "FSales": 1000.0,
                "FOP": 100.0,
                "FOdP": None,
                "FNP": None,
                "FEPS": None,
            }
        ]
    )

    features, state = build_forecast_revision_features_with_state(
        older, newer_state
    )

    assert features.empty
    assert state["7203|2027-03-31"]["FOP"] == pytest.approx(120.0)
