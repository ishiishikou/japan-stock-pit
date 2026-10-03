#!/usr/bin/env python3
import gzip
import hashlib
import io
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

import boto3
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import requests
from botocore.config import Config


API_URL = "https://api.e-stat.go.jp/rest/3.0/app/json/getStatsList"


def env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value.strip()


def b2_client():
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


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def extract_tables(body):
    root = body.get("GET_STATS_LIST") or {}
    datalist = root.get("DATALIST_INF") or {}
    tables = datalist.get("TABLE_INF") or []
    if isinstance(tables, dict):
        tables = [tables]
    return tables


def main():
    cfg = json.loads(
        Path(os.getenv("ESTAT_SEARCH_CONFIG", "config/estat_search_terms.json"))
        .read_text(encoding="utf-8")
    )
    app_id = env("ESTAT_APP_ID")
    s3 = b2_client()
    bucket = env("B2_BUCKET_NAME")
    now = datetime.now(timezone.utc)
    observed_at = now.isoformat().replace("+00:00", "Z")
    run_id = f"{now:%Y%m%dT%H%M%SZ}-{uuid.uuid4().hex[:8]}"

    frames = []
    results = []
    for term in cfg["search_terms"]:
        response = requests.get(
            API_URL,
            params={
                "appId": app_id,
                "lang": "J",
                "searchWord": term,
                "limit": int(cfg.get("limit_per_term", 100)),
                "explanationGetFlg": "Y",
            },
            timeout=90,
            headers={"Accept-Encoding": "gzip", "User-Agent": "japan-stock-pit/1.0"},
        )
        response.raise_for_status()
        payload = response.content
        digest = sha256(payload)
        safe_term = hashlib.sha1(term.encode("utf-8")).hexdigest()[:12]
        raw_key = (
            f"raw/estat/catalog/year={now:%Y}/month={now:%m}/day={now:%d}/"
            f"{safe_term}_{digest}.json.gz"
        )
        s3.put_object(
            Bucket=bucket,
            Key=raw_key,
            Body=gzip.compress(payload, compresslevel=9),
            ContentType="application/gzip",
            Metadata={"sha256": digest},
        )

        body = response.json()
        tables = extract_tables(body)
        results.append({"search_term": term, "tables": len(tables), "raw_key": raw_key})
        if not tables:
            continue
        frame = pd.json_normalize(tables, sep=".")
        frame.insert(0, "search_term", term)
        frame["source_id"] = "estat_api_v3"
        frame["dataset"] = "stats_catalog"
        frame["observed_at"] = observed_at
        frame["known_at"] = observed_at
        frame["payload_sha256"] = digest
        frame["ingestion_run_id"] = run_id
        frames.append(frame)

    if frames:
        combined = pd.concat(frames, ignore_index=True, sort=False)
        key = "normalized/estat/catalog/current.parquet"
        put_parquet(s3, bucket, key, combined)
    else:
        combined = pd.DataFrame()
        key = None

    manifest = {
        "source_id": "estat_api_v3",
        "dataset": "stats_catalog",
        "run_id": run_id,
        "observed_at": observed_at,
        "rows": len(combined),
        "normalized_key": key,
        "results": results,
    }
    manifest_key = (
        f"metadata/estat/runs/year={now:%Y}/month={now:%m}/day={now:%d}/"
        f"{run_id}.json"
    )
    s3.put_object(
        Bucket=bucket,
        Key=manifest_key,
        Body=json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8"),
        ContentType="application/json",
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
