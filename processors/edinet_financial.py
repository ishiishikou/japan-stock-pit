#!/usr/bin/env python3
import hashlib
import io
import json
import math
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

import boto3
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
from botocore.config import Config
from botocore.exceptions import ClientError


SOURCE_PREFIX = "normalized/edinet/xbrl_facts/category=financial/"
OUTPUT_PREFIX = "normalized/edinet/financial_metrics/"
MANIFEST_PREFIX = "metadata/edinet/financial_metrics/"

OUTPUT_COLUMNS = [
    "source_id",
    "dataset",
    "doc_id",
    "doc_type_code",
    "doc_description",
    "parent_doc_id",
    "edinet_code",
    "sec_code",
    "stock_code",
    "filer_name",
    "period_start",
    "period_end",
    "submit_datetime",
    "known_at",
    "observed_at",
    "metric",
    "element_id",
    "local_name",
    "item_name",
    "context_id",
    "relative_year",
    "consolidation",
    "period_type",
    "unit_id",
    "unit",
    "raw_value",
    "numeric_value",
    "source_parquet_key",
    "ingestion_run_id"
]


def is_b2_cap_exceeded(exc):
    if not isinstance(exc, ClientError):
        return False
    error = exc.response.get("Error", {})
    code = str(error.get("Code") or "")
    message = str(error.get("Message") or "").lower()
    return (
        code == "AccessDenied"
        and "cap exceeded" in message
        and ("download" in message or "class b" in message or "transaction" in message)
    )


def print_b2_cap_pause(error):
    print(json.dumps({
        "status": "paused",
        "reason": "b2_download_or_class_b_cap_exceeded",
        "error": str(error)[:500],
    }, ensure_ascii=False, indent=2))


def env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value.strip()


def client():
    endpoint = env("B2_ENDPOINT")
    if not endpoint.startswith(("http://", "https://")):
        endpoint = "https://" + endpoint
    host = endpoint.split("//", 1)[-1]
    region = host.split(".")[1] if host.startswith("s3.") else "us-east-005"
    return boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=env("B2_KEY_ID"),
        aws_secret_access_key=env("B2_APPLICATION_KEY"),
        region_name=region,
        config=Config(
            signature_version="s3v4",
            retries={"max_attempts": 2, "mode": "standard"},
            connect_timeout=10,
            read_timeout=30,
        ),
    )


def get_json(s3, bucket, key):
    try:
        body = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
        return json.loads(body)
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") in {"404", "NoSuchKey", "NotFound"}:
            return None
        raise


def put_json(s3, bucket, key, payload):
    s3.put_object(
        Bucket=bucket,
        Key=key,
        Body=json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8"),
        ContentType="application/json",
    )


def read_parquet(s3, bucket, key):
    body = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
    return pq.read_table(io.BytesIO(body)).to_pandas()


def put_parquet(s3, bucket, key, frame):
    table = pa.Table.from_pandas(frame, preserve_index=False)
    out = io.BytesIO()
    pq.write_table(table, out, compression="zstd")
    s3.put_object(
        Bucket=bucket,
        Key=key,
        Body=out.getvalue(),
        ContentType="application/vnd.apache.parquet",
    )


def list_parquet_keys(s3, bucket, prefix):
    token = None
    while True:
        kwargs = {"Bucket": bucket, "Prefix": prefix, "MaxKeys": 1000}
        if token:
            kwargs["ContinuationToken"] = token
        page = s3.list_objects_v2(**kwargs)
        for item in page.get("Contents") or []:
            key = item.get("Key") or ""
            if key.endswith(".parquet"):
                yield key
        if not page.get("IsTruncated"):
            break
        token = page.get("NextContinuationToken")


def local_name(element_id):
    text = str(element_id or "")
    return text.rsplit(":", 1)[-1] if ":" in text else text


def clean(value):
    if value is None:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    text = str(value).strip()
    return text or None


def parse_numeric(value):
    text = clean(value)
    if text is None:
        return None
    text = (
        text.replace(",", "")
        .replace("，", "")
        .replace(" ", "")
        .replace("　", "")
    )
    if text in {"-", "－", "―", "—", "△", "▲"}:
        return None
    negative = False
    if text.startswith(("△", "▲")):
        negative = True
        text = text[1:]
    if text.startswith("(") and text.endswith(")"):
        negative = True
        text = text[1:-1]
    text = re.sub(r"[^0-9eE+\-.]", "", text)
    if text in {"", "-", ".", "+", "-."}:
        return None
    try:
        number = float(text)
    except ValueError:
        return None
    return -number if negative else number


def first(frame, column):
    if column not in frame.columns:
        return None
    for value in frame[column].tolist():
        out = clean(value)
        if out is not None:
            return out
    return None


def load_alias_config(path):
    raw = Path(path).read_bytes()
    cfg = json.loads(raw)
    reverse = {}
    for metric, aliases in cfg["metrics"].items():
        for alias in aliases:
            reverse.setdefault(alias, []).append(metric)
    return {
        "aliases": reverse,
        "version": cfg.get("version"),
        "sha256": hashlib.sha256(raw).hexdigest(),
    }


def load_aliases(path):
    return load_alias_config(path)["aliases"]


def transform(frame, source_key, alias_map, run_id):
    required = {"doc_id", "element_id", "value", "context_id"}
    missing = required - set(frame.columns)
    if missing:
        raise RuntimeError(f"Missing financial fact columns: {sorted(missing)}")

    frame = frame.copy()
    frame["local_name"] = frame["element_id"].map(local_name)
    selected = frame[frame["local_name"].isin(alias_map.keys())].copy()

    rows = []
    metadata = {
        "source_id": "edinet_api_v2",
        "dataset": "financial_metrics",
        "doc_id": first(frame, "doc_id"),
        "doc_type_code": first(frame, "doc_type_code"),
        "doc_description": first(frame, "doc_description"),
        "parent_doc_id": first(frame, "parent_doc_id"),
        "edinet_code": first(frame, "edinet_code"),
        "sec_code": first(frame, "sec_code"),
        "stock_code": first(frame, "stock_code"),
        "filer_name": first(frame, "filer_name"),
        "period_start": first(frame, "period_start"),
        "period_end": first(frame, "period_end"),
        "submit_datetime": first(frame, "submit_datetime"),
        "known_at": first(frame, "known_at"),
        "observed_at": first(frame, "observed_at"),
        "source_parquet_key": source_key,
        "ingestion_run_id": run_id,
    }

    for _, fact in selected.iterrows():
        lname = str(fact["local_name"])
        for metric in alias_map.get(lname, []):
            rows.append(
                {
                    **metadata,
                    "metric": metric,
                    "element_id": clean(fact.get("element_id")),
                    "local_name": lname,
                    "item_name": clean(fact.get("item_name")),
                    "context_id": clean(fact.get("context_id")),
                    "relative_year": clean(fact.get("relative_year")),
                    "consolidation": clean(fact.get("consolidation")),
                    "period_type": clean(fact.get("period_type")),
                    "unit_id": clean(fact.get("unit_id")),
                    "unit": clean(fact.get("unit")),
                    "raw_value": clean(fact.get("value")),
                    "numeric_value": parse_numeric(fact.get("value")),
                }
            )

    return pd.DataFrame(rows, columns=OUTPUT_COLUMNS)


def main():
    alias_path = os.getenv(
        "EDINET_FINANCIAL_ALIASES", "config/financial_metric_elements.json"
    )
    alias_config = load_alias_config(alias_path)
    alias_map = alias_config["aliases"]
    alias_version = alias_config["version"]
    alias_sha256 = alias_config["sha256"]
    s3 = client()
    bucket = env("B2_BUCKET_NAME")
    now = datetime.now(timezone.utc)
    run_id = f"{now:%Y%m%dT%H%M%SZ}-{uuid.uuid4().hex[:8]}"

    processed = skipped = failed = rows_written = 0
    empty_docs = 0
    errors = []

    for source_key in list_parquet_keys(s3, bucket, SOURCE_PREFIX):
        doc_id = source_key.rsplit("/", 1)[-1].removesuffix(".parquet")
        manifest_key = f"{MANIFEST_PREFIX}doc_id={doc_id}.json"
        existing = get_json(s3, bucket, manifest_key)
        alias_config_matches = (
            existing
            and existing.get("alias_config_version") == alias_version
            and existing.get("alias_config_sha256") == alias_sha256
        )
        if (
            existing
            and existing.get("status") in {"ok", "no_matches"}
            and alias_config_matches
        ):
            skipped += 1
            continue

        try:
            frame = read_parquet(s3, bucket, source_key)
            out = transform(frame, source_key, alias_map, run_id)
            submit = first(frame, "submit_datetime")
            submit_date = submit[:10] if submit else "unknown"
            if out.empty:
                put_json(
                    s3,
                    bucket,
                    manifest_key,
                    {
                        "status": "no_matches",
                        "doc_id": doc_id,
                        "source_key": source_key,
                        "alias_config_version": alias_version,
                        "alias_config_sha256": alias_sha256,
                        "observed_at": now.isoformat().replace("+00:00", "Z"),
                        "ingestion_run_id": run_id,
                    },
                )
                empty_docs += 1
                continue

            output_key = (
                f"{OUTPUT_PREFIX}submit_date={submit_date}/{doc_id}.parquet"
            )
            put_parquet(s3, bucket, output_key, out)
            put_json(
                s3,
                bucket,
                manifest_key,
                {
                    "status": "ok",
                    "doc_id": doc_id,
                    "source_key": source_key,
                    "output_key": output_key,
                    "rows": len(out),
                    "metrics": sorted(out["metric"].unique().tolist()),
                    "alias_config_version": alias_version,
                    "alias_config_sha256": alias_sha256,
                    "observed_at": now.isoformat().replace("+00:00", "Z"),
                    "ingestion_run_id": run_id,
                },
            )
            processed += 1
            rows_written += len(out)
        except ClientError as exc:
            if is_b2_cap_exceeded(exc):
                raise
            failed += 1
            errors.append({"doc_id": doc_id, "error": str(exc)[:500]})
            put_json(
                s3,
                bucket,
                f"{MANIFEST_PREFIX}errors/doc_id={doc_id}/{run_id}.json",
                {
                    "status": "error",
                    "doc_id": doc_id,
                    "source_key": source_key,
                    "error": str(exc)[:1000],
                    "alias_config_version": alias_version,
                    "alias_config_sha256": alias_sha256,
                    "ingestion_run_id": run_id,
                },
            )
        except Exception as exc:
            failed += 1
            errors.append({"doc_id": doc_id, "error": str(exc)[:500]})
            put_json(
                s3,
                bucket,
                f"{MANIFEST_PREFIX}errors/doc_id={doc_id}/{run_id}.json",
                {
                    "status": "error",
                    "doc_id": doc_id,
                    "source_key": source_key,
                    "error": str(exc)[:1000],
                    "alias_config_version": alias_version,
                    "alias_config_sha256": alias_sha256,
                    "ingestion_run_id": run_id,
                },
            )

    manifest = {
        "source_id": "edinet_api_v2",
        "dataset": "financial_metrics",
        "run_id": run_id,
        "alias_config_version": alias_version,
        "alias_config_sha256": alias_sha256,
        "observed_at": now.isoformat().replace("+00:00", "Z"),
        "processed_documents": processed,
        "skipped_documents": skipped,
        "no_match_documents": empty_docs,
        "failed_documents": failed,
        "rows_written": rows_written,
        "errors": errors,
    }
    put_json(
        s3,
        bucket,
        (
            f"metadata/edinet/financial_metric_runs/year={now:%Y}/month={now:%m}/"
            f"day={now:%d}/{run_id}.json"
        ),
        manifest,
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    if failed and not processed and not empty_docs:
        raise RuntimeError("All unprocessed financial documents failed")


if __name__ == "__main__":
    try:
        main()
    except ClientError as exc:
        if is_b2_cap_exceeded(exc):
            print_b2_cap_pause(exc)
        else:
            raise
