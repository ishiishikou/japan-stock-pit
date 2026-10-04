#!/usr/bin/env python3
"""Discover unmatched EDINET financial XBRL elements for canonical metric aliases."""

import io
import json
import math
import os
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

import boto3
import pandas as pd
import pyarrow.parquet as pq
from botocore.config import Config


SOURCE_PREFIX = "normalized/edinet/xbrl_facts/category=financial/"
ALIAS_PATH = "config/financial_metric_elements.json"

TARGET_HINTS = {
    "interest_bearing_debt": (
        "debt", "borrow", "loan", "bond", "lease", "interestbearing",
        "有利子", "借入", "社債", "リース",
    ),
    "capital_expenditure": (
        "purchase", "acquisition", "propertyplant", "equipment", "intangible",
        "capitalexpenditure", "設備投資", "有形固定資産", "無形固定資産", "取得",
    ),
    "depreciation": (
        "depreciation", "amortization", "depreciationandamortization",
        "減価償却", "償却",
    ),
    "eps": (
        "earningspershare", "incomeper share", "eps", "１株", "1株",
        "一株", "基本的１株",
    ),
    "dividend_per_share": (
        "dividend", "dividendpershare", "配当", "１株当たり配当", "1株当たり配当",
    ),
    "income_tax": (
        "incometax", "taxexpense", "corporatetax", "法人税", "所得税", "税金費用",
    ),
}


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
        kwargs = {"Bucket": bucket, "Prefix": SOURCE_PREFIX, "MaxKeys": 1000}
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
    if isinstance(value, float) and math.isnan(value):
        return None
    text = str(value).strip()
    return text or None


def local_name(element_id):
    text = clean(element_id) or ""
    return text.rsplit(":", 1)[-1] if ":" in text else text


def load_aliases(path=ALIAS_PATH):
    cfg = json.loads(Path(path).read_text(encoding="utf-8"))
    return {
        alias
        for aliases in cfg["metrics"].values()
        for alias in aliases
    }


def matches_hint(local, item, hints):
    text = f"{local or ''} {item or ''}".lower()
    return any(hint.lower() in text for hint in hints)


def main():
    s3 = client()
    bucket = env("B2_BUCKET_NAME")
    aliases = load_aliases()
    now = datetime.now(timezone.utc)

    rows = Counter()
    numeric = Counter()
    docs = defaultdict(set)
    item_names = defaultdict(Counter)
    units = defaultdict(Counter)
    contexts = defaultdict(Counter)

    for key in list_keys(s3, bucket):
        frame = read_parquet(s3, bucket, key)
        if frame.empty or "element_id" not in frame.columns:
            continue

        doc_id = clean(frame["doc_id"].iloc[0]) if "doc_id" in frame.columns else key
        for _, row in frame.iterrows():
            lname = local_name(row.get("element_id"))
            if not lname or lname in aliases:
                continue

            rows[lname] += 1
            docs[lname].add(doc_id)
            item = clean(row.get("item_name"))
            if item:
                item_names[lname][item] += 1
            unit = clean(row.get("unit"))
            if unit:
                units[lname][unit] += 1
            context = clean(row.get("context_id"))
            if context:
                contexts[lname][context] += 1

            value = clean(row.get("value"))
            if value:
                normalized = (
                    value.replace(",", "")
                    .replace("，", "")
                    .replace(" ", "")
                    .replace("　", "")
                    .replace("△", "-")
                    .replace("▲", "-")
                    .replace("%", "")
                    .replace("％", "")
                )
                try:
                    float(normalized)
                    numeric[lname] += 1
                except ValueError:
                    pass

    elements = []
    for lname, count in rows.most_common():
        top_item = item_names[lname].most_common(1)
        item = top_item[0][0] if top_item else None
        elements.append(
            {
                "local_name": lname,
                "item_name": item,
                "rows": count,
                "documents": len(docs[lname]),
                "numeric_rows": numeric[lname],
                "numeric_ratio": round(numeric[lname] / count, 4) if count else 0,
                "top_units": units[lname].most_common(5),
                "top_contexts": contexts[lname].most_common(5),
            }
        )

    candidates = {}
    for metric, hints in TARGET_HINTS.items():
        matched = [
            element
            for element in elements
            if matches_hint(element["local_name"], element["item_name"], hints)
        ]
        matched.sort(
            key=lambda x: (x["documents"], x["numeric_rows"], x["rows"]),
            reverse=True,
        )
        candidates[metric] = matched[:50]

    report = {
        "observed_at": now.isoformat().replace("+00:00", "Z"),
        "dataset": "edinet_financial_alias_discovery",
        "source_prefix": SOURCE_PREFIX,
        "unmatched_elements": len(elements),
        "targets": candidates,
        "top_unmatched_elements": elements[:200],
    }

    body = json.dumps(report, ensure_ascii=False, indent=2).encode("utf-8")
    latest = "metadata/edinet/financial_alias_discovery/latest.json"
    history = (
        "metadata/edinet/financial_alias_discovery/history/"
        f"year={now:%Y}/month={now:%m}/day={now:%d}/{now:%Y%m%dT%H%M%SZ}.json"
    )
    s3.put_object(Bucket=bucket, Key=latest, Body=body, ContentType="application/json")
    s3.put_object(Bucket=bucket, Key=history, Body=body, ContentType="application/json")

    # Keep Action logs focused enough to inspect manually.
    compact = {
        "observed_at": report["observed_at"],
        "unmatched_elements": report["unmatched_elements"],
        "targets": {name: items[:50] for name, items in candidates.items()},
    }
    print(json.dumps(compact, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
