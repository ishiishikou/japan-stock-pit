import pandas as pd

from analysis.universe import (
    build_universe_snapshot,
    filter_to_point_in_time_universe,
)


def test_build_universe_snapshot_supports_alphanumeric_security_code():
    master = pd.DataFrame(
        [
            {
                "Date": "2026-06-01",
                "Code": "130A0",
                "CoName": "Example",
                "Mkt": "011",
                "MktNm": "Prime",
                "ProdCat": "011",
                "known_at": "2026-06-01T06:00:00Z",
                "observed_at": "2026-08-24T00:00:00Z",
            }
        ]
    )

    out = build_universe_snapshot(master)

    assert out.iloc[0]["ticker"] == "130A"
    assert out.iloc[0]["jquants_code"] == "130A0"


def test_point_in_time_universe_does_not_backfill_future_listing():
    master = pd.DataFrame(
        [
            {
                "Date": "2026-01-01",
                "Code": "11110",
                "known_at": "2026-01-01T06:00:00Z",
                "observed_at": "2026-03-26T00:00:00Z",
            },
            {
                "Date": "2026-02-01",
                "Code": "11110",
                "known_at": "2026-02-01T06:00:00Z",
                "observed_at": "2026-04-26T00:00:00Z",
            },
            {
                "Date": "2026-02-01",
                "Code": "22220",
                "known_at": "2026-02-01T06:00:00Z",
                "observed_at": "2026-04-26T00:00:00Z",
            },
        ]
    )
    universe = build_universe_snapshot(master)
    factor = pd.DataFrame(
        [
            {"date": "2026-01-01", "ticker": "1111", "factor": 1.0},
            {"date": "2026-01-01", "ticker": "2222", "factor": 2.0},
            {"date": "2026-02-01", "ticker": "2222", "factor": 3.0},
        ]
    )

    out = filter_to_point_in_time_universe(factor, universe)

    assert list(zip(out["date"], out["ticker"])) == [
        ("2026-01-01", "1111"),
        ("2026-02-01", "2222"),
    ]


def test_universe_filters_are_explicit():
    master = pd.DataFrame(
        [
            {
                "Date": "2026-01-01",
                "Code": "11110",
                "Mkt": "011",
                "ProdCat": "011",
                "known_at": "2026-01-01T06:00:00Z",
                "observed_at": "2026-03-26T00:00:00Z",
            },
            {
                "Date": "2026-01-01",
                "Code": "22220",
                "Mkt": "012",
                "ProdCat": "999",
                "known_at": "2026-01-01T06:00:00Z",
                "observed_at": "2026-03-26T00:00:00Z",
            },
        ]
    )

    out = build_universe_snapshot(
        master,
        allowed_markets={"011"},
        allowed_product_categories={"011"},
    )

    assert out["ticker"].tolist() == ["1111"]
