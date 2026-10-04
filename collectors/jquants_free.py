#!/usr/bin/env python3
import io
import json
import os
import sys
import uuid
from datetime import datetime, time, timedelta, timezone
from pathlib import Path

import boto3
import jquantsapi
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
from botocore.config import Config
from botocore.exceptions import ClientError


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from analysis.universe import build_universe_snapshot
from features.jquants_forecast import build_forecast_revision_features_with_state


JST = timezone(timedelta(hours=9))
FREE_DELAY_DAYS = 84
FORECAST_STATE_KEY = "metadata/jquants/forecast_revision/state.json"


def env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value.strip()


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
    print(
        json.dumps(
            {
                "status": "paused",
                "reason": "b2_download_or_class_b_cap_exceeded",
                "error": str(error)[:500],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


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


def get_json(s3, bucket: str, key: str):
    try:
        body = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
        return json.loads(body)
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") in {"404", "NoSuchKey", "NotFound"}:
            return None
        raise


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


def _parse_disc_time(value):
    if value is None or pd.isna(value):
        return time(23, 59, 59)
    text = str(value).strip()
    if not text or text.lower() in {"nan", "nat", "none"}:
        return time(23, 59, 59)

    # J-Quants currently returns HH:MM:SS, but accept common compact variants.
    for fmt in ("%H:%M:%S", "%H:%M", "%H%M%S", "%H%M"):
        try:
            return datetime.strptime(text, fmt).time()
        except ValueError:
            pass
    return time(23, 59, 59)


def known_at_for_financial_summary_row(row, fallback_date: str) -> str:
    parsed_date = pd.to_datetime(row.get("DiscDate"), errors="coerce")
    if pd.isna(parsed_date):
        return known_at_for_date(fallback_date)
    dt = datetime.combine(
        parsed_date.date(),
        _parse_disc_time(row.get("DiscTime")),
        tzinfo=JST,
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
    if dataset == "financial_summary":
        out["known_at"] = out.apply(
            lambda row: known_at_for_financial_summary_row(row, target_date),
            axis=1,
        )
    else:
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

    # Read the tiny forecast state before spending J-Quants API calls. If B2 is
    # temporarily capped, this turns the whole run into a clean pause.
    state_payload = get_json(s3, bucket, FORECAST_STATE_KEY) or {}
    forecast_state = state_payload.get("entries") or {}

    # Official ClientV2 reads JQUANTS_API_KEY from the environment.
    cli = jquantsapi.ClientV2()

    datasets = {}
    datasets["master"] = cli.get_eq_master(date=target_text)
    datasets["daily_bars"] = cli.get_eq_bars_daily(date_yyyymmdd=target_text)
    datasets["financial_summary"] = cli.get_fin_summary(date_yyyymmdd=target_text)

    results = {}
    normalized_frames = {}
    for name, frame in datasets.items():
        normalized = normalize(frame, name, target_text, observed_at)
        normalized_frames[name] = normalized
        results[name] = {
            "rows": int(len(normalized)),
            "columns": list(normalized.columns) if not normalized.empty else [],
        }
        if normalized.empty:
            continue
        key = f"normalized/jquants/{name}/date={target_text}/current.parquet"
        put_parquet(s3, bucket, key, normalized)
        results[name]["key"] = key

    master = normalized_frames.get("master", pd.DataFrame())
    universe = build_universe_snapshot(master) if not master.empty else pd.DataFrame()
    results["universe"] = {"rows": int(len(universe))}
    if not universe.empty:
        universe_key = f"normalized/jquants/universe/date={target_text}/current.parquet"
        put_parquet(s3, bucket, universe_key, universe)
        results["universe"]["key"] = universe_key

    financial_summary = normalized_frames.get("financial_summary", pd.DataFrame())
    forecast_features = pd.DataFrame()
    if not financial_summary.empty:
        forecast_features, forecast_state = build_forecast_revision_features_with_state(
            financial_summary,
            forecast_state,
        )

    results["forecast_revision_features"] = {
        "rows": int(len(forecast_features)),
    }
    if not forecast_features.empty:
        feature_key = (
            f"features/jquants/forecast_revision/date={target_text}/current.parquet"
        )
        put_parquet(s3, bucket, feature_key, forecast_features)
        results["forecast_revision_features"]["key"] = feature_key

    put_json(
        s3,
        bucket,
        FORECAST_STATE_KEY,
        {
            "version": 1,
            "updated_at": observed_at,
            "last_target_date": target_text,
            "entries": forecast_state,
        },
    )

    manifest = {
        "source_id": "jquants_v2",
        "run_id": run_id,
        "observed_at": observed_at,
        "target_date": target_text,
        "free_plan_assumed_delay_days": FREE_DELAY_DAYS,
        "results": results,
        "note": (
            "J-Quants Free plan is delayed. observed_at records archive retrieval time. "
            "For financial_summary, known_at uses DiscDate/DiscTime from the disclosure; "
            "other daily datasets use the target-date end of day."
        ),
    }
    key = (
        f"metadata/jquants/runs/year={now:%Y}/month={now:%m}/day={now:%d}/"
        f"{run_id}.json"
    )
    put_json(s3, bucket, key, manifest)
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    try:
        main()
    except ClientError as exc:
        if is_b2_cap_exceeded(exc):
            print_b2_cap_pause(exc)
        else:
            raise
