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


class B2ReadCapExceeded(RuntimeError):
    """Raised only when Backblaze explicitly reports a bandwidth/Class B cap."""


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
        config=Config(
            signature_version="s3v4",
            retries={"max_attempts": 2, "mode": "standard"},
            connect_timeout=10,
            read_timeout=20,
        ),
    )


def is_b2_read_cap_exceeded(exc):
    error = exc.response.get("Error", {}) if getattr(exc, "response", None) else {}
    code = str(error.get("Code") or "")
    message = str(error.get("Message") or "")
    text = f"{code} {message}".lower()
    return (
        code == "AccessDenied"
        and "cap exceeded" in text
        and ("bandwidth" in text or "class b" in text or "transaction" in text)
    )


def raise_if_b2_read_cap_exceeded(exc):
    if is_b2_read_cap_exceeded(exc):
        raise B2ReadCapExceeded(
            "Backblaze B2 download bandwidth/Class B transaction cap exceeded"
        ) from exc


def get_json(s3, bucket, key):
    try:
        return json.loads(s3.get_object(Bucket=bucket, Key=key)["Body"].read())
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") in {"404", "NoSuchKey", "NotFound"}:
            return None
        raise_if_b2_read_cap_exceeded(exc)
        raise


def prefix_stats(s3, bucket, prefix):
    count = 0
    size = 0
    token = None
    while True:
        kwargs = {"Bucket": bucket, "Prefix": prefix, "MaxKeys": 1000}
        if token:
            kwargs["ContinuationToken"] = token
        try:
            page = s3.list_objects_v2(**kwargs)
        except ClientError as exc:
            raise_if_b2_read_cap_exceeded(exc)
            raise
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
        try:
            page = s3.list_objects_v2(**kwargs)
        except ClientError as exc:
            raise_if_b2_read_cap_exceeded(exc)
            raise
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


def put_report_best_effort(s3, bucket, key, body):
    try:
        s3.put_object(
            Bucket=bucket,
            Key=key,
            Body=body,
            ContentType="application/json",
        )
        return True
    except ClientError as exc:
        # A read cap normally should not block uploads, but health reporting
        # must not turn a known quota condition into another failed workflow.
        if is_b2_read_cap_exceeded(exc):
            return False
        raise


def degraded_report(now, reason):
    return {
        "observed_at": now.isoformat().replace("+00:00", "Z"),
        "status": "degraded",
        "issues": ["b2_read_cap_exceeded"],
        "observation_error": reason,
        "storage": None,
        "edinet_backfill_state": None,
        "prefixes": {},
        "latest_runs": {},
    }


def main():
    s3 = client()
    bucket = env("B2_BUCKET_NAME")
    now = datetime.now(timezone.utc)

    try:
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
    except B2ReadCapExceeded as exc:
        # Stop further reads immediately. This is an observability limitation,
        # not evidence that the PIT datasets themselves are unhealthy.
        report = degraded_report(now, str(exc))

    body = json.dumps(report, ensure_ascii=False, indent=2, default=str).encode("utf-8")
    latest_written = put_report_best_effort(
        s3, bucket, "metadata/health/latest.json", body
    )
    history = (
        f"metadata/health/history/year={now:%Y}/month={now:%m}/day={now:%d}/"
        f"{now:%Y%m%dT%H%M%SZ}.json"
    )
    history_written = put_report_best_effort(s3, bucket, history, body)

    report["report_write"] = {
        "latest": latest_written,
        "history": history_written,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
