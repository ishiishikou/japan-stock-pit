#!/usr/bin/env python3
import io
import json
import os
import uuid
from datetime import datetime, timedelta, timezone

import boto3
import jquantsapi
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
from botocore.config import Config


JST = timezone(timedelta(hours=9))
FREE_DELAY_DAYS = 84


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


def put_parquet(s3, bucket: str, key: str, frame: pd.DataFrame):
    table = pa.Table.from_pandas(frame, preserve_index=False)
    out = io.BytesIO()
    pq.write_table(table, out, compression="zstd")
    s3.put_object(
        Bucket=bucket,
        Key=key,
        Body=out.getvalue(),
        ContentType="application/vnd.apache.parquet",
    )


def put_json(s3, bucket: str, key: str, payload: dict):
    s3.put_object(
        Bucket=bucket,
        Key=key,
        Body=json.dumps(payload, ensure_ascii=False, indent=2, default=str).encode("utf-8"),
        ContentType="application/json",
    )


def known_at_for_date(date_text: str) -> str:
    dt = datetime.fromisoformat(date_text).replace(
        hour=23, minute=59, second=59, tzinfo=JST
    )
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def normalize(frame: pd.DataFrame, dataset: str, target_date: str, observed_at: str):
    if frame is None or frame.empty:
        return pd.DataFrame()
    out = frame.copy()
    for col in out.columns:
        if pd.api.types.is_datetime64_any_dtype(out[col]):
            out[col] = out[col].astype(str)
    out["source_id"] = "jquants_v2"
    out["dataset"] = dataset
    out["retrieval_target_date"] = target_date
    out["observed_at"] = observed_at
    out["known_at"] = known_at_for_date(target_date)
    return out


def main():
    s3 = b2_client()
    bucket = env("B2_BUCKET_NAME")
    now = datetime.now(timezone.utc)
    observed_at = now.isoformat().replace("+00:00", "Z")
    today_jst = now.astimezone(JST).date()
    target = today_jst - timedelta(days=FREE_DELAY_DAYS)
    target_text = target.isoformat()
    run_id = f"{now:%Y%m%dT%H%M%SZ}-{uuid.uuid4().hex[:8]}"

    # Official ClientV2 reads JQUANTS_API_KEY from the environment.
    cli = jquantsapi.ClientV2()

    datasets = {}
    datasets["master"] = cli.get_eq_master(date=target_text)
    datasets["daily_bars"] = cli.get_eq_bars_daily(date_yyyymmdd=target_text)
    datasets["financial_summary"] = cli.get_fin_summary(date_yyyymmdd=target_text)

    results = {}
    for name, frame in datasets.items():
        normalized = normalize(frame, name, target_text, observed_at)
        results[name] = {
            "rows": int(len(normalized)),
            "columns": list(normalized.columns) if not normalized.empty else [],
        }
        if normalized.empty:
            continue
        key = f"normalized/jquants/{name}/date={target_text}/current.parquet"
        put_parquet(s3, bucket, key, normalized)
        results[name]["key"] = key

    manifest = {
        "source_id": "jquants_v2",
        "run_id": run_id,
        "observed_at": observed_at,
        "target_date": target_text,
        "free_plan_assumed_delay_days": FREE_DELAY_DAYS,
        "results": results,
        "note": (
            "J-Quants Free plan is delayed. known_at is conservatively based on the "
            "original data date, while observed_at records when this archive retrieved it."
        ),
    }
    key = (
        f"metadata/jquants/runs/year={now:%Y}/month={now:%m}/day={now:%d}/"
        f"{run_id}.json"
    )
    put_json(s3, bucket, key, manifest)
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
