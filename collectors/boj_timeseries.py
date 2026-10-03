#!/usr/bin/env python3
import gzip
import hashlib
import io
import json
import os
import sys
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path

import boto3
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import requests
from botocore.config import Config
from botocore.exceptions import ClientError


JST = timezone(timedelta(hours=9))


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


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def parse_last_update(value):
    if value in (None, "", "null"):
        return None, None
    text = str(value)
    dt = datetime.strptime(text, "%Y%m%d").replace(tzinfo=JST)
    known = dt.replace(hour=23, minute=59, second=59).astimezone(timezone.utc)
    return dt.date().isoformat(), known.isoformat().replace("+00:00", "Z")


def read_json_object(s3, bucket, key, default):
    try:
        body = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
        return json.loads(body)
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") in {"NoSuchKey", "404"}:
            return default
        raise


def read_parquet_object(s3, bucket, key):
    try:
        body = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
        return pq.read_table(io.BytesIO(body)).to_pandas()
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") in {"NoSuchKey", "404"}:
            return None
        raise


def put_parquet(s3, bucket, key, df: pd.DataFrame):
    table = pa.Table.from_pandas(df, preserve_index=False)
    out = io.BytesIO()
    pq.write_table(table, out, compression="zstd")
    s3.put_object(
        Bucket=bucket,
        Key=key,
        Body=out.getvalue(),
        ContentType="application/vnd.apache.parquet",
    )


def normalize_response(item, payload_hash, observed_at, run_id, source_url, dataset, db):
    rows = []
    values = item.get("VALUES") or {}
    dates = values.get("SURVEY_DATES") or []
    data_values = values.get("VALUES") or []
    if len(dates) != len(data_values):
        raise RuntimeError(
            f"BOJ response length mismatch for {db}/{item.get('SERIES_CODE')}: "
            f"{len(dates)} dates vs {len(data_values)} values"
        )

    source_last_update, known_at = parse_last_update(item.get("LAST_UPDATE"))
    for period_code, raw_value in zip(dates, data_values):
        value = None
        if raw_value not in (None, "", "NA", "N/A"):
            try:
                value = float(raw_value)
            except (TypeError, ValueError):
                value = None
        rows.append(
            {
                "source_id": "boj_timeseries",
                "dataset": dataset,
                "db": db,
                "series_id": str(item.get("SERIES_CODE", "")),
                "series_name": item.get("NAME_OF_TIME_SERIES"),
                "period_code": str(period_code),
                "value": value,
                "frequency": item.get("FREQUENCY"),
                "unit": item.get("UNIT"),
                "category": item.get("CATEGORY"),
                "source_last_update": source_last_update,
                "known_at": known_at or observed_at,
                "observed_at": observed_at,
                "source_url": source_url,
                "payload_sha256": payload_hash,
                "ingestion_run_id": run_id,
            }
        )
    return rows


def canonical_compare_value(value):
    if pd.isna(value):
        return None
    if isinstance(value, float):
        return round(value, 12)
    return value


def find_changes(current: pd.DataFrame, previous: pd.DataFrame | None):
    key_cols = ["source_id", "dataset", "db", "series_id", "period_code"]
    if previous is None or previous.empty:
        result = current.copy()
        result["change_type"] = "new"
        return result

    previous_map = {}
    for _, row in previous.iterrows():
        key = tuple(str(row[c]) for c in key_cols)
        previous_map[key] = (
            canonical_compare_value(row.get("value")),
            str(row.get("source_last_update")),
        )

    changed = []
    for _, row in current.iterrows():
        key = tuple(str(row[c]) for c in key_cols)
        old = previous_map.get(key)
        now = (
            canonical_compare_value(row.get("value")),
            str(row.get("source_last_update")),
        )
        if old is None:
            r = row.to_dict()
            r["change_type"] = "new"
            changed.append(r)
        elif old != now:
            r = row.to_dict()
            r["change_type"] = "revised"
            changed.append(r)

    return pd.DataFrame(changed)


def main():
    config_path = Path(os.getenv("BOJ_CONFIG", "config/boj_macro_series.json"))
    cfg = json.loads(config_path.read_text(encoding="utf-8"))
    s3 = b2_client()
    bucket = env("B2_BUCKET_NAME")

    now = datetime.now(timezone.utc)
    observed_at = now.isoformat().replace("+00:00", "Z")
    run_id = f"{now:%Y%m%dT%H%M%SZ}-{uuid.uuid4().hex[:8]}"

    hash_manifest_key = "metadata/boj_macro/latest_raw_hashes.json"
    hash_manifest = read_json_object(s3, bucket, hash_manifest_key, {})
    new_manifest = dict(hash_manifest)

    all_rows = []
    for spec in cfg["series"]:
        params = {
            "format": "json",
            "lang": cfg.get("language", "en"),
            "db": spec["db"],
            "code": spec["code"],
            "startDate": spec["start_date"],
        }
        response = requests.get(
            cfg["endpoint"],
            params=params,
            timeout=60,
            headers={"Accept-Encoding": "gzip", "User-Agent": "japan-stock-pit/1.0"},
        )
        response.raise_for_status()
        payload = response.content
        payload_hash = sha256_bytes(payload)
        body = response.json()
        if int(body.get("STATUS", 0)) != 200:
            raise RuntimeError(f"BOJ API error: {body}")
        if body.get("NEXTPOSITION") not in (None, "", 0, "0"):
            raise RuntimeError(
                f"BOJ API returned pagination for {spec['db']}/{spec['code']}; "
                "collector must be extended before continuing."
            )

        manifest_id = f"{spec['db']}:{spec['code']}"
        if hash_manifest.get(manifest_id) != payload_hash:
            raw_key = (
                f"raw/boj_macro/year={now:%Y}/month={now:%m}/day={now:%d}/"
                f"{spec['db']}_{spec['code']}_{run_id}.json.gz"
            )
            compressed = gzip.compress(payload, compresslevel=9)
            s3.put_object(
                Bucket=bucket,
                Key=raw_key,
                Body=compressed,
                ContentType="application/gzip",
                Metadata={"sha256": payload_hash},
            )
            new_manifest[manifest_id] = payload_hash

        source_url = response.url
        resultset = body.get("RESULTSET") or []
        for item in resultset:
            all_rows.extend(
                normalize_response(
                    item=item,
                    payload_hash=payload_hash,
                    observed_at=observed_at,
                    run_id=run_id,
                    source_url=source_url,
                    dataset=spec["dataset"],
                    db=spec["db"],
                )
            )

    if not all_rows:
        raise RuntimeError("BOJ API returned no rows")

    current = pd.DataFrame(all_rows).sort_values(
        ["dataset", "db", "series_id", "period_code"]
    ).reset_index(drop=True)

    current_key = "normalized/boj_macro/current.parquet"
    previous = read_parquet_object(s3, bucket, current_key)
    changes = find_changes(current, previous)

    if not changes.empty:
        changes["observed_at"] = observed_at
        changes["ingestion_run_id"] = run_id
        change_key = (
            f"normalized/boj_macro/changes/year={now:%Y}/month={now:%m}/day={now:%d}/"
            f"{run_id}.parquet"
        )
        put_parquet(s3, bucket, change_key, changes)
        print(f"Stored {len(changes)} new/revised PIT rows at {change_key}")
    else:
        print("No BOJ value revisions or new observations detected.")

    put_parquet(s3, bucket, current_key, current)

    manifest_payload = json.dumps(new_manifest, sort_keys=True, indent=2).encode()
    s3.put_object(
        Bucket=bucket,
        Key=hash_manifest_key,
        Body=manifest_payload,
        ContentType="application/json",
    )

    run_manifest = {
        "source_id": "boj_timeseries",
        "run_id": run_id,
        "observed_at": observed_at,
        "current_rows": len(current),
        "changed_rows": len(changes),
        "series_requested": len(cfg["series"]),
    }
    manifest_key = (
        f"metadata/boj_macro/runs/year={now:%Y}/month={now:%m}/day={now:%d}/"
        f"{run_id}.json"
    )
    s3.put_object(
        Bucket=bucket,
        Key=manifest_key,
        Body=json.dumps(run_manifest, indent=2).encode(),
        ContentType="application/json",
    )
    print(json.dumps(run_manifest, indent=2))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise
