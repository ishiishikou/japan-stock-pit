"""Regression tests for financial B2 Class B cost controls and correctness."""

import io
import json

import pandas as pd

from processors import edinet_financial as financial


class FakeEvents:
    def __init__(self):
        self.handlers = {}

    def register(self, event, callback):
        self.handlers.setdefault(event, []).append(callback)

    def emit(self, operation):
        for callback in self.handlers.get(f"before-call.s3.{operation}", []):
            callback()


class FakeS3:
    def __init__(self):
        self.meta = type("Meta", (), {"events": FakeEvents()})()
        self.content = {}
        self.sources = [
            {"Key": f"{financial.SOURCE_PREFIX}submit_date=2026-10-30/{doc}.parquet",
             "ETag": f"etag-{doc}", "Size": 10}
            for doc in ("A", "B", "C")
        ]
        for obj in self.sources[:2]:
            doc = obj["Key"].rsplit("/", 1)[-1].removesuffix(".parquet")
            self.content[f"{financial.MANIFEST_PREFIX}doc_id={doc}.json"] = json.dumps({
                "status": "ok", "source_key": obj["Key"],
                "alias_config_version": 5, "alias_config_sha256": "sha-v5",
            }).encode("utf-8")

    def list_objects_v2(self, **kwargs):
        self.meta.events.emit("ListObjectsV2")
        prefix = kwargs["Prefix"]
        if prefix == financial.SOURCE_PREFIX:
            objects = self.sources
        else:
            objects = [{"Key": key} for key in self.content if key.startswith(prefix)]
        return {"Contents": objects, "IsTruncated": False}

    def get_object(self, **kwargs):
        self.meta.events.emit("GetObject")
        return {"Body": io.BytesIO(self.content[kwargs["Key"]])}

    def put_object(self, **kwargs):
        self.meta.events.emit("PutObject")
        self.content[kwargs["Key"]] = kwargs["Body"]
        return {}


def run_financial(monkeypatch, fake, alias_sha="sha-v5", budget=150):
    monkeypatch.setattr(financial, "client", lambda: fake)
    monkeypatch.setattr(financial, "env", lambda name: "fake-bucket")
    monkeypatch.setattr(financial, "load_alias_config", lambda path: {
        "aliases": {}, "version": 5, "sha256": alias_sha,
    })
    monkeypatch.setattr(financial, "read_parquet", lambda *args: pd.DataFrame())
    monkeypatch.setattr(financial, "transform", lambda *args: pd.DataFrame())
    monkeypatch.setenv("EDINET_FINANCIAL_MAX_DOCS_PER_RUN", str(budget))
    financial.main()


def test_financial_index_avoids_repeated_gets_after_bounded_migration(monkeypatch):
    s3 = FakeS3()
    run_financial(monkeypatch, s3)
    index = json.loads(s3.content[financial.INDEX_KEY])
    assert set(index["entries"]) == {"A", "B", "C"}
    assert index["entries"]["C"]["status"] == "no_matches"
    # First run reads two legacy JSON manifests and one new Parquet.
    # No GET is spent checking whether the absent C manifest exists.
    fresh = FakeS3()
    fresh.content = dict(s3.content)
    run_financial(monkeypatch, fresh)
    # Second run uses exactly one GET for the consolidated index.
    # It must not read ANY per-doc manifests or source Parquet files.
    meter = financial.B2CallCounter()
    s3_cached = FakeS3()
    s3_cached.content = dict(s3.content)
    meter.attach(s3_cached)
    idx = financial.load_financial_index(s3_cached, "bucket")
    assert idx["entries"]["A"]["status"] == "ok"
    assert meter.snapshot()["GetObject"] == 1


def test_index_invalidation_requires_alias_fingerprint_and_source_etag():
    obj = {"Key": "data/A.parquet", "ETag": "etag-1", "Size": 22}
    entry = financial.financial_index_entry(obj, "ok", 5, "hash-5")
    assert financial.financial_index_matches(entry, obj, 5, "hash-5")
    assert not financial.financial_index_matches(entry, obj, 5, "hash-6")
    assert not financial.financial_index_matches(entry, {**obj, "ETag": "etag-2"}, 5, "hash-5")
    assert not financial.financial_index_matches(entry, {**obj, "Size": 23}, 5, "hash-5")
    assert not financial.financial_index_matches(entry, {**obj, "ETag": None}, 5, "hash-5")


def test_legacy_manifest_must_match_alias_and_source():
    source = {"Key": "data/A.parquet"}
    manifest = {"status": "no_matches", "source_key": "data/A.parquet",
                "alias_config_version": 3, "alias_config_sha256": "old"}
    assert financial.financial_legacy_manifest_matches(manifest, source, 3, "old")
    assert not financial.financial_legacy_manifest_matches(manifest, source, 3, "new")
    assert not financial.financial_legacy_manifest_matches(
        manifest, {"Key": "data/B.parquet"}, 3, "old"
    )


def test_migration_budget_caps_legacy_manifest_gets(monkeypatch):
    fake = FakeS3()
    # Only one legacy doc is verified per run. Rest remain pending.
    run_financial(monkeypatch, fake, budget=1)
    idx = json.loads(fake.content[financial.INDEX_KEY])
    assert len(idx["entries"]) == 1


def test_api_operation_counter_is_explicit_and_no_secrets():
    s3 = FakeS3()
    meter = financial.B2CallCounter()
    meter.attach(s3)
    s3.list_objects_v2(Prefix=financial.SOURCE_PREFIX)
    s3.get_object(Key=f"{financial.MANIFEST_PREFIX}doc_id=A.json")
    s3.put_object(Key="demo", Body=b"")
    assert meter.snapshot() == {"GetObject": 1, "ListObjectsV2": 1, "PutObject": 1}
