#!/usr/bin/env python3
import hashlib
import io
import json
import os
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

import boto3
import pandas as pd
import pyarrow.parquet as pq
from botocore.config import Config


PREFIX = "normalized/edinet/financial_metrics/"
ALIAS_PATH = "config/financial_metric_elements.json"


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


def list_keys(s3, bucket):
    token = None
    while True:
        kwargs = {"Bucket": bucket, "Prefix": PREFIX, "MaxKeys": 1000}
        if token:
            kwargs["ContinuationToken"] = token
        page = s3.list_objects_v2(**kwargs)
        for obj in page.get("Contents") or []:
            key = obj.get("Key") or ""
            if key.endswith(".parquet"):
                yield key
        if not page.get("IsTruncated"):
            break
        token = page.get("NextContinuationToken")


def read_parquet(s3, bucket, key):
    body = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
    return pq.read_table(io.BytesIO(body)).to_pandas()


def clean(value):
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def alias_fingerprint(path=ALIAS_PATH):
    raw = Path(path).read_bytes()
    cfg = json.loads(raw)
    return cfg.get("version"), hashlib.sha256(raw).hexdigest()


def main():
    s3 = client()
    bucket = env("B2_BUCKET_NAME")
    now = datetime.now(timezone.utc)
    alias_version, alias_sha256 = alias_fingerprint()

    metric_rows = Counter()
    metric_docs = defaultdict(set)
    metric_tickers = defaultdict(set)
    metric_numeric = Counter()
    contexts = Counter()
    relative_years = Counter()
    consolidations = Counter()
    period_types = Counter()
    units = Counter()
    doc_types = Counter()
    total_rows = 0
    total_docs = 0

    for key in list_keys(s3, bucket):
        frame = read_parquet(s3, bucket, key)
        if frame.empty:
            continue
        total_docs += 1
        total_rows += len(frame)

        doc_id = clean(frame["doc_id"].iloc[0]) if "doc_id" in frame.columns else key
        doc_type = clean(frame["doc_type_code"].iloc[0]) if "doc_type_code" in frame.columns else None
        if doc_type:
            doc_types[doc_type] += 1

        for _, row in frame.iterrows():
            metric = clean(row.get("metric"))
            if not metric:
                continue
            metric_rows[metric] += 1
            metric_docs[metric].add(doc_id)
            ticker = clean(row.get("stock_code"))
            if ticker:
                metric_tickers[metric].add(ticker)
            value = row.get("numeric_value")
            if pd.notna(value):
                metric_numeric[metric] += 1

            for counter, column in [
                (contexts, "context_id"),
                (relative_years, "relative_year"),
                (consolidations, "consolidation"),
                (period_types, "period_type"),
                (units, "unit"),
            ]:
                item = clean(row.get(column))
                if item:
                    counter[item] += 1

    metrics = []
    for metric in sorted(metric_rows):
        rows = metric_rows[metric]
        numeric = metric_numeric[metric]
        metrics.append(
            {
                "metric": metric,
                "rows": rows,
                "documents": len(metric_docs[metric]),
                "tickers": len(metric_tickers[metric]),
                "numeric_rows": numeric,
                "numeric_ratio": round(numeric / rows, 4) if rows else 0,
            }
        )

    report = {
        "observed_at": now.isoformat().replace("+00:00", "Z"),
        "dataset": "edinet_financial_metric_coverage",
        "alias_config_version": alias_version,
        "alias_config_sha256": alias_sha256,
        "total_documents": total_docs,
        "total_rows": total_rows,
        "metrics": metrics,
        "document_types": dict(doc_types.most_common()),
        "top_relative_years": relative_years.most_common(30),
        "top_consolidations": consolidations.most_common(30),
        "top_period_types": period_types.most_common(30),
        "top_units": units.most_common(30),
        "top_contexts": contexts.most_common(50),
    }

    body = json.dumps(report, ensure_ascii=False, indent=2).encode("utf-8")
    s3.put_object(
        Bucket=bucket,
        Key="metadata/edinet/financial_metric_coverage/latest.json",
        Body=body,
        ContentType="application/json",
    )
    history = (
        f"metadata/edinet/financial_metric_coverage/history/"
        f"year={now:%Y}/month={now:%m}/day={now:%d}/{now:%Y%m%dT%H%M%SZ}.json"
    )
    s3.put_object(Bucket=bucket, Key=history, Body=body, ContentType="application/json")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
