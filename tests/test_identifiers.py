import pandas as pd
import pytest

from processors.identifiers import build_identifier_map, normalize_security_code


def test_normalize_security_code_supports_alphanumeric_codes():
    assert normalize_security_code("72030") == "7203"
    assert normalize_security_code(72030.0) == "7203"
    assert normalize_security_code("130A0") == "130A"
    assert normalize_security_code("130A") == "130A"
    assert normalize_security_code("unexpected") is None


def test_identifier_map_joins_edinet_and_jquants_conservatively():
    edinet = pd.DataFrame(
        [
            {
                "sec_code": "72030",
                "edinet_code": "E02144",
                "filer_name": "トヨタ自動車株式会社",
                "known_at": "2026-06-20T06:00:00Z",
                "observed_at": "2026-06-20T07:00:00Z",
            }
        ]
    )
    jquants = pd.DataFrame(
        [
            {
                "Code": "72030",
                "CoName": "トヨタ自動車",
                "known_at": "2026-06-21T05:00:00Z",
                "observed_at": "2026-09-13T00:00:00Z",
            }
        ]
    )

    out = build_identifier_map(edinet, jquants)

    assert len(out) == 1
    row = out.iloc[0]
    assert row["ticker"] == "7203"
    assert row["sec_code"] == "72030"
    assert row["jquants_code"] == "72030"
    assert row["edinet_code"] == "E02144"
    assert bool(row["code_match"]) is True
    assert row["company_name"] == "トヨタ自動車"
    assert row["known_at"] == "2026-06-21T05:00:00Z"
    assert row["observed_at"] == "2026-09-13T00:00:00Z"


def test_identifier_map_keeps_one_source_only_rows():
    edinet = pd.DataFrame(
        [
            {
                "sec_code": "130A0",
                "edinet_code": "E99999",
                "filer_name": "Example",
                "known_at": "2026-06-20T06:00:00Z",
                "observed_at": "2026-06-20T07:00:00Z",
            }
        ]
    )
    jquants = pd.DataFrame(
        columns=["Code", "CoName", "known_at", "observed_at"]
    )

    out = build_identifier_map(edinet, jquants)

    assert out.iloc[0]["ticker"] == "130A"
    assert out.iloc[0]["edinet_code"] == "E99999"
    assert pd.isna(out.iloc[0]["jquants_code"])
    assert out.iloc[0]["code_match"] is None


def test_identifier_map_uses_latest_record_per_source():
    edinet = pd.DataFrame(
        [
            {
                "sec_code": "12340",
                "edinet_code": "EOLD",
                "filer_name": "Old",
                "known_at": "2026-01-01T00:00:00Z",
                "observed_at": "2026-01-01T01:00:00Z",
            },
            {
                "sec_code": "12340",
                "edinet_code": "ENEW",
                "filer_name": "New",
                "known_at": "2026-02-01T00:00:00Z",
                "observed_at": "2026-02-01T01:00:00Z",
            },
        ]
    )
    jquants = pd.DataFrame(
        columns=["Code", "CoName", "known_at", "observed_at"]
    )

    out = build_identifier_map(edinet, jquants)

    assert out.iloc[0]["edinet_code"] == "ENEW"
