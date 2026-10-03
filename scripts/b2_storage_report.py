#!/usr/bin/env python3
import json
import os
from collections import defaultdict
from datetime import datetime, timezone

import boto3
from botocore.config import Config


REFERENCE_BUDGET_BYTES = 10 * 1024**3


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
        config=Config(signature_version="s3v4"),
    )


def main():
    s3 = client()
    bucket = env("B2_BUCKET_NAME")
    totals = defaultdict(lambda: {"bytes": 0, "objects": 0})
    grand_bytes = 0
    grand_objects = 0
    token = None

    while True:
        kwargs = {"Bucket": bucket, "MaxKeys": 1000}
        if token:
            kwargs["ContinuationToken"] = token
        page = s3.list_objects_v2(**kwargs)
        for obj in page.get("Contents") or []:
            key = obj["Key"]
            size = int(obj.get("Size") or 0)
            top = key.split("/", 1)[0] if "/" in key else "(root)"
            second = "/".join(key.split("/")[:2]) if "/" in key else "(root)"
            for label in (top, second):
                totals[label]["bytes"] += size
                totals[label]["objects"] += 1
            grand_bytes += size
            grand_objects += 1
        if not page.get("IsTruncated"):
            break
        token = page.get("NextContinuationToken")

    ratio = grand_bytes / REFERENCE_BUDGET_BYTES
    if ratio >= 0.95:
        level = "critical"
    elif ratio >= 0.85:
        level = "warning"
    elif ratio >= 0.70:
        level = "watch"
    else:
        level = "ok"

    now = datetime.now(timezone.utc)
    report = {
        "observed_at": now.isoformat().replace("+00:00", "Z"),
        "bucket": bucket,
        "object_count": grand_objects,
        "bytes": grand_bytes,
        "gib": round(grand_bytes / 1024**3, 4),
        "reference_budget_gib": 10,
        "reference_budget_ratio": round(ratio, 6),
        "level": level,
        "by_prefix": {
            key: {
                "bytes": value["bytes"],
                "gib": round(value["bytes"] / 1024**3, 4),
                "objects": value["objects"],
            }
            for key, value in sorted(totals.items())
        },
    }

    body = json.dumps(report, ensure_ascii=False, indent=2).encode("utf-8")
    s3.put_object(
        Bucket=bucket,
        Key="metadata/storage/latest.json",
        Body=body,
        ContentType="application/json",
    )
    history_key = (
        f"metadata/storage/history/year={now:%Y}/month={now:%m}/day={now:%d}/"
        f"{now:%Y%m%dT%H%M%SZ}.json"
    )
    s3.put_object(
        Bucket=bucket,
        Key=history_key,
        Body=body,
        ContentType="application/json",
    )

    print(json.dumps(report, ensure_ascii=False, indent=2))
    if level == "watch":
        print("::notice::B2 usage is above 70% of the 10 GiB reference budget.")
    elif level == "warning":
        print("::warning::B2 usage is above 85% of the 10 GiB reference budget.")
    elif level == "critical":
        print("::error::B2 usage is above 95% of the 10 GiB reference budget.")


if __name__ == "__main__":
    main()
