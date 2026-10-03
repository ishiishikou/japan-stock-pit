from datetime import datetime, timezone

from processors.edinet_financial import parse_numeric
from processors.edinet_ownership import local_name, parse_number
from collectors.edinet_packages import known_at_for_document


def test_parse_financial_numeric():
    assert parse_numeric("1,234") == 1234.0
    assert parse_numeric("△1,234") == -1234.0
    assert parse_numeric("(42)") == -42.0


def test_parse_ownership_ratio():
    assert parse_number("5.25%") == 0.0525
    assert parse_number("1,234") == 1234.0


def test_local_name():
    assert local_name("jpcrp_cor:NameOfIssuer") == "NameOfIssuer"


def test_known_at_prefers_later_operation_time():
    observed = datetime(2026, 10, 3, 12, tzinfo=timezone.utc)
    doc = {
        "submitDateTime": "2026-10-03 10:00",
        "opeDateTime": "2026-10-03 11:00",
    }
    assert known_at_for_document(doc, observed) == "2026-10-03T02:00:00Z"
