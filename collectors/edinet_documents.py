#!/usr/bin/env python3
import argparse
import gzip
import hashlib
import io
import json
import os
import sys
import uuid
from datetime import date, datetime, timedelta, timezone

import boto3
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import requests
from botocore.config import Config
from botocore.exceptions import ClientError


JST = timezone(timedelta(hours=9))
EDINET_BASE_URL = "https://api.edinet-fsa.go.jp/api/v2/documents.json"

SOURCE_FIELDS = [
    "seqNumber",
    "docID",
    "edinetCode",
    "secCode",
    "JCN",
    "filerName",
    "fundCode",
    "ordinanceCode",
    "formCode",
    "docTypeCode",
    "periodStart",
    "periodEnd",
    "submitDateTime",
    "docDescription",
    "issuerEdinetCode",
    "subjectEdinetCode",
    "subsidiaryEdinetCode",
    "currentReportReason",
    "parentDocID",
    "opeDateTime",
    "withdrawalStatus",
    "docInfoEditStatus",
    "disclosureStatus",
    "xbrlFlag",
    "pdfFlag",
    "attachDocFlag",
    "englishDocFlag",
    "csvFlag",
    "legalStatus",
]

COLUMN_MAP = {
    "seqNumber": "seq_number",
    "docID": "doc_id",
    "edinetCode": "edinet_code",
    "secCode": "sec_code",
    "JCN": "jcn",
    "filerName": "filer_name",
    "fundCode": "fund_code",
    "ordinanceCode": "ordinance_code",
    "formCode": "form_code",
    "docTypeCode": "doc_type_code",
    "periodStart": "period_start",
    "periodEnd": "period_end",
    "submitDateTime": "submit_datetime",
    "docDescription": "doc_description",
    "issuerEdinetCode": "issuer_edinet_code",
    "subjectEdinetCode": "subject_edinet_code",
    "subsidiaryEdinetCode": "subsidiary_edinet_code",
    "currentReportReason": "current_report_reason",
    "parentDocID": "parent_doc_id",
    "opeDateTime": "operation_datetime",
    "withdrawalStatus": "withdrawal_status",
    "docInfoEditStatus": "doc_info_edit_status",
    "disclosureStatus": "disclosure_status",
    "xbrlFlag": "xbrl_flag",
    "pdfFlag": "pdf_flag",
    "attachDocFlag": "attach_doc_flag",
    "englishDocFlag": "english_doc_flag",
    "csvFlag": "csv_flag",
    "legalStatus": "legal_status",
}

NORMALIZED_COLUMNS = [
    "source_id",
    "dataset",
    "file_date",
    *COLUMN_MAP.values(),
    "stock_code",
    "known_at",
    "observed_at",
    "source_process_at",
    "source_url",
    "payload_sha256",
    "record_sha256",
    "ingestion_run_id",
]


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


def utc_iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_jst_timestamp(value):
    if value in (None, "", "null"):
        return None
    try:
        dt = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=JST)
    return dt


def object_exists(s3, bucket: str, key: str) -> bool:
    try:
        s3.head_object(Bucket=bucket, Key=key)
        return True
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") in {"404", "NoSuchKey", "NotFound"}:
            return False
        raise


def read_parquet_object(s3, bucket: str, key: str):
    try:
        body = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
        return pq.read_table(io.BytesIO(body)).to_pandas()
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") in {"404", "NoSuchKey", "NotFound"}:
            return None
        raise


def put_parquet(s3, bucket: str, key: str, df: pd.DataFrame):
    table = pa.Table.from_pandas(df, preserve_index=False)
    out = io.BytesIO()
    pq.write_table(table, out, compression="zstd")
    s3.put_object(
        Bucket=bucket,
        Key=key,
        Body=out.getvalue(),
        ContentType="application/vnd.apache.parquet",
    )


def safe_source_url(file_date: str) -> str:
    return f"{EDINET_BASE_URL}?date={file_date}&type=2"


def record_hash(record: dict) -> str:
    source_record = {name: record.get(name) for name in SOURCE_FIELDS}
    canonical = json.dumps(
        source_record,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return sha256_bytes(canonical)


def derive_known_at(record: dict, source_process_dt: datetime | None, observed_dt: datetime) -> str:
    candidates = []
    for field in ("submitDateTime", "opeDateTime"):
        parsed = parse_jst_timestamp(record.get(field))
        if parsed is not None:
            candidates.append(parsed)
    if candidates:
        return utc_iso(max(candidates))
    if source_process_dt is not None:
        return utc_iso(source_process_dt)
    return utc_iso(observed_dt)


def normalize_payload(
    body: dict,
    file_date: str,
    payload_hash: str,
    observed_dt: datetime,
    run_id: str,
) -> pd.DataFrame:
    metadata = body.get("metadata") or {}
    status = str(metadata.get("status", ""))
    if status and status != "200":
        raise RuntimeError(f"EDINET API error status={status}: {metadata.get('message')}")

    source_process_dt = parse_jst_timestamp(metadata.get("processDateTime"))
    source_process_at = utc_iso(source_process_dt) if source_process_dt else None
    observed_at = utc_iso(observed_dt)
    rows = []

    for record in body.get("results") or []:
        row = {
            "source_id": "edinet_api_v2",
            "dataset": "document_list",
            "file_date": file_date,
        }
        for source_name, normalized_name in COLUMN_MAP.items():
            row[normalized_name] = record.get(source_name)

        sec_code = record.get("secCode")
        row["stock_code"] = (
            str(sec_code)[:4]
            if sec_code not in (None, "") and len(str(sec_code)) >= 4
            else None
        )
        row["known_at"] = derive_known_at(record, source_process_dt, observed_dt)
        row["observed_at"] = observed_at
        row["source_process_at"] = source_process_at
        row["source_url"] = safe_source_url(file_date)
        row["payload_sha256"] = payload_hash
        row["record_sha256"] = record_hash(record)
        row["ingestion_run_id"] = run_id
        rows.append(row)

    return pd.DataFrame(rows, columns=NORMALIZED_COLUMNS)


def find_changes(current: pd.DataFrame, previous: pd.DataFrame | None) -> pd.DataFrame:
    if current.empty:
        return pd.DataFrame(columns=[*NORMALIZED_COLUMNS, "change_type"])

    if previous is None or previous.empty:
        result = current.copy()
        result["change_type"] = "new"
        return result

    previous_map = {}
    for _, row in previous.iterrows():
        key = (str(row.get("seq_number")), str(row.get("doc_id")))
        previous_map[key] = str(row.get("record_sha256"))

    changed_rows = []
    for _, row in current.iterrows():
        key = (str(row.get("seq_number")), str(row.get("doc_id")))
        old_hash = previous_map.get(key)
        new_hash = str(row.get("record_sha256"))
        if old_hash is None or old_hash != new_hash:
            data = row.to_dict()
            data["change_type"] = "new" if old_hash is None else "revised"
            if old_hash is not None:
                # If EDINET does not expose an exact edit time, observed_at is the
                # conservative PIT boundary for the revised record version.
                data["known_at"] = row.get("operation_datetime") or row.get("observed_at")
                op_dt = parse_jst_timestamp(row.get("operation_datetime"))
                if op_dt is not None:
                    data["known_at"] = utc_iso(op_dt)
            changed_rows.append(data)

    return pd.DataFrame(changed_rows, columns=[*NORMALIZED_COLUMNS, "change_type"])


def fetch_document_list(api_key: str, file_date: str):
    response = requests.get(
        EDINET_BASE_URL,
        params={
            "date": file_date,
            "type": "2",
            "Subscription-Key": api_key,
        },
        timeout=90,
        headers={
            "Accept": "application/json",
            "Accept-Encoding": "gzip",
            "User-Agent": "japan-stock-pit/1.0",
        },
    )
    response.raise_for_status()
    body = response.json()
    return response.content, body


def collect_date(s3, bucket: str, api_key: str, target: date, run_id: str, observed_dt: datetime):
    file_date = target.isoformat()
    payload, body = fetch_document_list(api_key, file_date)
    payload_hash = sha256_bytes(payload)

    raw_key = (
        f"raw/edinet/document_list/file_date={file_date}/"
        f"sha256={payload_hash}.json.gz"
    )
    if not object_exists(s3, bucket, raw_key):
        s3.put_object(
            Bucket=bucket,
            Key=raw_key,
            Body=gzip.compress(payload, compresslevel=9),
            ContentType="application/gzip",
            Metadata={"sha256": payload_hash},
        )

    current = normalize_payload(body, file_date, payload_hash, observed_dt, run_id)
    current_key = (
        f"normalized/edinet/document_list/file_date={file_date}/current.parquet"
    )
    previous = read_parquet_object(s3, bucket, current_key)
    changes = find_changes(current, previous)

    if not changes.empty:
        now = observed_dt.astimezone(timezone.utc)
        change_key = (
            f"normalized/edinet/document_list/changes/year={now:%Y}/month={now:%m}/"
            f"day={now:%d}/file_date={file_date}/{run_id}.parquet"
        )
        put_parquet(s3, bucket, change_key, changes)
        print(f"{file_date}: stored {len(changes)} new/revised rows at {change_key}")
    else:
        print(f"{file_date}: no new/revised rows")

    put_parquet(s3, bucket, current_key, current)

    metadata = body.get("metadata") or {}
    return {
        "file_date": file_date,
        "rows": len(current),
        "changed_rows": len(changes),
        "payload_sha256": payload_hash,
        "source_process_datetime": metadata.get("processDateTime"),
    }


def parse_args():
    parser = argparse.ArgumentParser(description="Collect EDINET API v2 document lists.")
    parser.add_argument(
        "--date",
        help="Collect one EDINET file date (YYYY-MM-DD). Overrides --lookback-days.",
    )
    parser.add_argument(
        "--lookback-days",
        type=int,
        default=int(os.getenv("EDINET_LOOKBACK_DAYS", "3")),
        help="Number of JST calendar days to collect including today (default: 3).",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    if args.lookback_days < 1 or args.lookback_days > 90:
        raise ValueError("--lookback-days must be between 1 and 90")

    api_key = env("EDINET_API_KEY")
    s3 = b2_client()
    bucket = env("B2_BUCKET_NAME")
    observed_dt = datetime.now(timezone.utc)
    run_id = f"{observed_dt:%Y%m%dT%H%M%SZ}-{uuid.uuid4().hex[:8]}"

    if args.date:
        targets = [date.fromisoformat(args.date)]
    else:
        today_jst = observed_dt.astimezone(JST).date()
        targets = [
            today_jst - timedelta(days=offset)
            for offset in range(args.lookback_days - 1, -1, -1)
        ]

    results = []
    for target in targets:
        results.append(
            collect_date(
                s3=s3,
                bucket=bucket,
                api_key=api_key,
                target=target,
                run_id=run_id,
                observed_dt=observed_dt,
            )
        )

    run_manifest = {
        "source_id": "edinet_api_v2",
        "dataset": "document_list",
        "run_id": run_id,
        "observed_at": utc_iso(observed_dt),
        "dates_requested": [d.isoformat() for d in targets],
        "total_rows": sum(item["rows"] for item in results),
        "total_changed_rows": sum(item["changed_rows"] for item in results),
        "results": results,
    }
    now = observed_dt.astimezone(timezone.utc)
    manifest_key = (
        f"metadata/edinet/runs/year={now:%Y}/month={now:%m}/day={now:%d}/"
        f"{run_id}.json"
    )
    s3.put_object(
        Bucket=bucket,
        Key=manifest_key,
        Body=json.dumps(run_manifest, ensure_ascii=False, indent=2).encode("utf-8"),
        ContentType="application/json",
    )
    print(json.dumps(run_manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise
