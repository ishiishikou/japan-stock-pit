#!/usr/bin/env python3
import json
import os
from datetime import datetime, timezone

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError


PREFIXES = [
    "raw/edinet/csv_packages/",
    "normalized/edinet/xbrl_facts/",
    "normalized/edinet/ownership_summary/",
    "normalized/edinet/financial_metrics/",
    "normalized/boj_macro/",
    "normalized/jquants/",
    "normalized/estat/",
]


def env(name):
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
        config=Config(signature_version="s3v4"),
    )


def get_json(s3, bucket, key):
    try:
        return json.loads(s3.get_object(Bucket=bucket, Key=key)["Body"].read())
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") in {"404", "NoSuchKey", "NotFound"}:
            return None
        raise


def prefix_stats(s3, bucket, prefix):
    count = 0
    size = 0
    token = None
    while True:
        kwargs = {"Bucket": bucket, "Prefix": prefix, "MaxKeys": 1000}
        if token:
            kwargs["ContinuationToken"] = token
        page = s3.list_objects_v2(**kwargs)
        for obj in page.get("Contents") or []:
            count += 1
            size += int(obj.get("Size") or 0)
        if not page.get("IsTruncated"):
            break
        token = page.get("NextContinuationToken")
    return {"objects": count, "bytes": size, "mib": round(size / 1024**2, 3)}


def latest_json_under(s3, bucket, prefix):
    latest = None
    token = None
    while True:
        kwargs = {"Bucket": bucket, "Prefix": prefix, "MaxKeys": 1000}
        if token:
            kwargs["ContinuationToken"] = token
        page = s3.list_objects_v2(**kwargs)
        for obj in page.get("Contents") or []:
            key = obj.get("Key") or ""
            if not key.endswith(".json"):
                continue
            candidate = (obj.get("LastModified"), key)
            if latest is None or candidate > latest:
                latest = candidate
        if not page.get("IsTruncated"):
            break
        token = page.get("NextContinuationToken")
    return get_json(s3, bucket, latest[1]) if latest else None


def main():
    s3 = client()
    bucket = env("B2_BUCKET_NAME")
    now = datetime.now(timezone.utc)

    storage = get_json(s3, bucket, "metadata/storage/latest.json")
    backfill = get_json(s3, bucket, "metadata/edinet/backfill/state.json")

    prefix_summary = {
        prefix: prefix_stats(s3, bucket, prefix)
        for prefix in PREFIXES
    }

    latest_runs = {
        "edinet_packages": latest_json_under(s3, bucket, "metadata/edinet/package_runs/"),
        "edinet_ownership": latest_json_under(s3, bucket, "metadata/edinet/ownership_runs/"),
        "edinet_financial": latest_json_under(s3, bucket, "metadata/edinet/financial_metric_runs/"),
        "edinet_backfill": latest_json_under(s3, bucket, "metadata/edinet/backfill/runs/"),
        "boj": latest_json_under(s3, bucket, "metadata/boj_macro/runs/"),
        "jquants": latest_json_under(s3, bucket, "metadata/jquants/runs/"),
        "estat": latest_json_under(s3, bucket, "metadata/estat/runs/"),
    }

    issues = []
    if storage and storage.get("level") in {"warning", "critical"}:
        issues.append(f"storage_{storage.get('level')}")
    for name, run in latest_runs.items():
        if not run:
            continue
        failures = int(run.get("failed_documents") or 0)
        if failures:
            issues.append(f"{name}_failures={failures}")
        if run.get("status") == "error":
            issues.append(f"{name}_status_error")

    report = {
        "observed_at": now.isoformat().replace("+00:00", "Z"),
        "status": "ok" if not issues else "attention",
        "issues": issues,
        "storage": storage,
        "edinet_backfill_state": backfill,
        "prefixes": prefix_summary,
        "latest_runs": latest_runs,
    }

    body = json.dumps(report, ensure_ascii=False, indent=2, default=str).encode("utf-8")
    s3.put_object(
        Bucket=bucket,
        Key="metadata/health/latest.json",
        Body=body,
        ContentType="application/json",
    )
    history = f"metadata/health/history/year={now:%Y}/month={now:%m}/day={now:%d}/{now:%Y%m%dT%H%M%SZ}.json"
    s3.put_object(Bucket=bucket, Key=history, Body=body, ContentType="application/json")
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
