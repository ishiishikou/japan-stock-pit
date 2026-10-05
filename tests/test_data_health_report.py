from botocore.exceptions import ClientError
import pytest

from scripts.data_health_report import (
    B2ReadCapExceeded,
    is_b2_read_cap_exceeded,
    raise_if_b2_read_cap_exceeded,
)


def client_error(code, message):
    return ClientError(
        {"Error": {"Code": code, "Message": message}},
        "GetObject",
    )


def test_detects_backblaze_read_cap_exceeded():
    exc = client_error(
        "AccessDenied",
        "Cannot download file, download bandwidth or transaction "
        "(Class B) cap exceeded. See the Caps & Alerts page to increase your cap.",
    )
    assert is_b2_read_cap_exceeded(exc) is True

    with pytest.raises(B2ReadCapExceeded):
        raise_if_b2_read_cap_exceeded(exc)


def test_does_not_mask_generic_access_denied():
    exc = client_error("AccessDenied", "Access Denied")
    assert is_b2_read_cap_exceeded(exc) is False
    raise_if_b2_read_cap_exceeded(exc)
