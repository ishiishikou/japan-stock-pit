from botocore.exceptions import ClientError

from collectors.edinet_documents import is_b2_cap_exceeded as documents_cap
from collectors.edinet_packages import (
    is_b2_cap_exceeded as packages_cap,
    list_successful_package_doc_ids,
)
from processors.edinet_ownership import (
    is_b2_cap_exceeded as ownership_cap,
    list_successful_ownership_doc_ids,
)
from scripts.b2_storage_report import is_b2_cap_exceeded as storage_cap


def client_error(code, message):
    return ClientError(
        {"Error": {"Code": code, "Message": message}},
        "GetObject",
    )


def test_all_b2_readers_recognize_explicit_cap_only():
    cap = client_error(
        "AccessDenied",
        "Cannot download file, download bandwidth or transaction "
        "(Class B) cap exceeded.",
    )
    generic = client_error("AccessDenied", "Access Denied")

    for detector in (documents_cap, packages_cap, ownership_cap, storage_cap):
        assert detector(cap) is True
        assert detector(generic) is False


class FakeS3:
    def list_objects_v2(self, **kwargs):
        prefix = kwargs["Prefix"]
        if prefix == "metadata/edinet/packages/doc_id=":
            keys = [
                f"{prefix}S100AAA.json",
                f"{prefix}S100BBB.json",
                "metadata/edinet/package_errors/doc_id=S100ERR/run.json",
            ]
        else:
            keys = [
                f"{prefix}S200AAA.json",
                f"{prefix}S200BBB.json",
                f"{prefix}errors/doc_id=S200ERR/run.json",
            ]
        return {
            "Contents": [{"Key": key} for key in keys],
            "IsTruncated": False,
        }


def test_success_manifest_listing_avoids_per_document_gets():
    s3 = FakeS3()
    assert list_successful_package_doc_ids(s3, "bucket") == {"S100AAA", "S100BBB"}
    assert list_successful_ownership_doc_ids(s3, "bucket") == {"S200AAA", "S200BBB"}
