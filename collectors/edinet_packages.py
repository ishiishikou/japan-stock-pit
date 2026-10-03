#!/usr/bin/env python3
import argparse
import hashlib
import io
import json
import os
import sys
import time
import uuid
import zipfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import boto3
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import requests
from botocore.config import Config
from botocore.exceptions import ClientError


JST = timezone(timedelta(hours=9))
LIST_URL = "https://api.edinet-fsa.go.jp/api/v2/documents.json"
DOCUMENT_URL = "https://api.edinet-fsa.go.jp/api/v2/documents/{doc_id}"

CSV_COLUMN_MAP = {
    "要素ID": "element_id",
    "項目名": "item_name",
    "コンテキストID": "context_id",
    "相対年度": "relative_year",
    "連結・個別": "consolidation",
    "期間・時点": "period_type",
    "ユニットID": "unit_id",
    "単位": "unit",
    "値": "value",
    # Defensive English aliases in case EDINET adds an English export variant.
    "Element ID": "element_id",
    "Item Name": "item_name",
    "Context ID": "context_id",
    "Relative Year": "relative_year",
    "Consolidated/Non-consolidated": "consolidation",
    "Period/Instant": "period_type",
    "Unit ID": "unit_id",
    "Unit": "unit",
    "Value": "value",
}

FACT_COLUMNS = [
    "source_id",
    "dataset",
    "category",
    "doc_id",
    "doc_type_code",
    "doc_description",
    "edinet_code",
    "sec_code",
    "stock_code",
    "filer_name",
    "issuer_edinet_code",
    "subject_edinet_code",
    "parent_doc_id",
    "period_start",
    "period_end",
    "submit_datetime",
    "known_at",
    "observed_at",
    "csv_file_name",
    "element_id",
    "item_name",
    "context_id",
    "relative_year",
    "consolidation",
    "period_type",
    "unit_id",
    "unit",
    "value",
    "package_sha256",
    "csv_sha256",
    "source_url",
    "ingestion_run_id",
]


def env(name: str, default=None) -> str:
    value = os.getenv(name)
    if value is None or value == "":
        if default is not None:
            return str(default)
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
    text = str(value).strip()
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=JST)
    return dt


def known_at_for_document(doc: dict, observed_dt: datetime) -> str:
    candidates = []
    for field in ("submitDateTime", "opeDateTime"):
        dt = parse_jst_timestamp(doc.get(field))
        if dt is not None:
            candidates.append(dt)
    return utc_iso(max(candidates)) if candidates else utc_iso(observed_dt)


def get_json(s3, bucket: str, key: str):
    try:
        body = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
        return json.loads(body)
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") in {"404", "NoSuchKey", "NotFound"}:
            return None
        raise


def put_json(s3, bucket: str, key: str, payload: dict):
    s3.put_object(
        Bucket=bucket,
        Key=key,
        Body=json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8"),
        ContentType="application/json",
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


def request_with_retry(url: str, params: dict, timeout: int = 120, attempts: int = 4):
    last_error = None
    for attempt in range(1, attempts + 1):
        try:
            response = requests.get(
                url,
                params=params,
                timeout=timeout,
                headers={
                    "Accept-Encoding": "gzip",
                    "User-Agent": "japan-stock-pit/1.0",
                },
            )
            if response.status_code in {429, 500, 502, 503, 504}:
                raise requests.HTTPError(
                    f"retryable status {response.status_code}",
                    response=response,
                )
            response.raise_for_status()
            return response
        except requests.RequestException as exc:
            last_error = exc
            if attempt == attempts:
                break
            time.sleep(min(30, 2 ** attempt))
    raise RuntimeError(f"EDINET request failed after {attempts} attempts: {last_error}")


def fetch_list(api_key: str, target_date: date):
    response = request_with_retry(
        LIST_URL,
        params={
            "date": target_date.isoformat(),
            "type": "2",
            "Subscription-Key": api_key,
        },
        timeout=90,
    )
    body = response.json()
    metadata = body.get("metadata") or {}
    status = str(metadata.get("status", ""))
    if status and status != "200":
        raise RuntimeError(
            f"EDINET list API error for {target_date}: "
            f"status={status} message={metadata.get('message')}"
        )
    return body


def load_rules(path: Path):
    cfg = json.loads(path.read_text(encoding="utf-8"))
    by_code = {}
    for category, spec in cfg["categories"].items():
        for code in spec["codes"]:
            by_code[str(code)] = {
                "category": category,
                "priority": int(spec.get("priority", 99)),
                "download_csv": bool(spec.get("download_csv", True)),
            }
    return cfg, by_code


def select_documents(body: dict, rules_by_code: dict):
    selected = []
    for doc in body.get("results") or []:
        code = str(doc.get("docTypeCode") or "")
        rule = rules_by_code.get(code)
        if not rule or not rule["download_csv"]:
            continue
        if str(doc.get("csvFlag") or "0") != "1":
            continue
        if not doc.get("docID"):
            continue
        selected.append(
            {
                **doc,
                "_category": rule["category"],
                "_priority": rule["priority"],
            }
        )
    selected.sort(
        key=lambda d: (
            int(d["_priority"]),
            str(d.get("submitDateTime") or ""),
            str(d.get("docID") or ""),
        )
    )
    return selected


def fetch_csv_package(api_key: str, doc_id: str):
    response = request_with_retry(
        DOCUMENT_URL.format(doc_id=doc_id),
        params={"type": "5", "Subscription-Key": api_key},
        timeout=180,
    )
    payload = response.content
    if not payload.startswith(b"PK"):
        content_type = response.headers.get("Content-Type")
        raise RuntimeError(
            f"EDINET {doc_id} type=5 did not return ZIP; "
            f"content_type={content_type!r}, size={len(payload)}"
        )
    return payload


def read_edinet_csv(raw: bytes) -> pd.DataFrame:
    errors = []
    for encoding in ("utf-16", "utf-16-le", "utf-16-be"):
        try:
            return pd.read_csv(
                io.BytesIO(raw),
                sep="\t",
                dtype=str,
                keep_default_na=False,
                encoding=encoding,
                engine="python",
            )
        except (UnicodeError, pd.errors.ParserError) as exc:
            errors.append(f"{encoding}: {exc}")
    raise RuntimeError("Unable to parse EDINET CSV: " + " | ".join(errors))


def normalized_facts_from_zip(payload: bytes, doc: dict, observed_dt: datetime, run_id: str):
    rows = []
    known_at = known_at_for_document(doc, observed_dt)
    package_hash = sha256_bytes(payload)
    submit_date = str(doc.get("submitDateTime") or "")[:10]
    stock_code = None
    if doc.get("secCode"):
        stock_code = str(doc["secCode"])[:4]

    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        members = [
            name
            for name in archive.namelist()
            if name.lower().endswith(".csv")
            and "xbrl_to_csv" in name.lower().replace("\\", "/")
        ]
        if not members:
            # Keep raw package, but surface this as a parse problem so the next run can retry.
            raise RuntimeError(f"No XBRL_TO_CSV/*.csv found in EDINET package {doc['docID']}")

        for name in members:
            csv_bytes = archive.read(name)
            frame = read_edinet_csv(csv_bytes)
            renamed = {}
            for column in frame.columns:
                key = str(column).strip().lstrip("\ufeff")
                if key in CSV_COLUMN_MAP:
                    renamed[column] = CSV_COLUMN_MAP[key]
            frame = frame.rename(columns=renamed)

            missing = [col for col in ("element_id", "context_id", "value") if col not in frame.columns]
            if missing:
                raise RuntimeError(
                    f"Unexpected EDINET CSV columns in {name}; "
                    f"missing={missing}, columns={list(frame.columns)}"
                )

            csv_hash = sha256_bytes(csv_bytes)
            for expected in CSV_COLUMN_MAP.values():
                if expected not in frame.columns:
                    frame[expected] = ""

            for _, fact in frame.iterrows():
                rows.append(
                    {
                        "source_id": "edinet_api_v2",
                        "dataset": "xbrl_csv_facts",
                        "category": doc["_category"],
                        "doc_id": doc.get("docID"),
                        "doc_type_code": str(doc.get("docTypeCode") or ""),
                        "doc_description": doc.get("docDescription"),
                        "edinet_code": doc.get("edinetCode"),
                        "sec_code": doc.get("secCode"),
                        "stock_code": stock_code,
                        "filer_name": doc.get("filerName"),
                        "issuer_edinet_code": doc.get("issuerEdinetCode"),
                        "subject_edinet_code": doc.get("subjectEdinetCode"),
                        "parent_doc_id": doc.get("parentDocID"),
                        "period_start": doc.get("periodStart"),
                        "period_end": doc.get("periodEnd"),
                        "submit_datetime": doc.get("submitDateTime"),
                        "known_at": known_at,
                        "observed_at": utc_iso(observed_dt),
                        "csv_file_name": name,
                        "element_id": fact.get("element_id", ""),
                        "item_name": fact.get("item_name", ""),
                        "context_id": fact.get("context_id", ""),
                        "relative_year": fact.get("relative_year", ""),
                        "consolidation": fact.get("consolidation", ""),
                        "period_type": fact.get("period_type", ""),
                        "unit_id": fact.get("unit_id", ""),
                        "unit": fact.get("unit", ""),
                        "value": fact.get("value", ""),
                        "package_sha256": package_hash,
                        "csv_sha256": csv_hash,
                        "source_url": DOCUMENT_URL.format(doc_id=doc["docID"]) + "?type=5",
                        "ingestion_run_id": run_id,
                    }
                )

    return pd.DataFrame(rows, columns=FACT_COLUMNS)


def process_document(
    s3,
    bucket: str,
    api_key: str,
    doc: dict,
    observed_dt: datetime,
    run_id: str,
):
    doc_id = str(doc["docID"])
    manifest_key = f"metadata/edinet/packages/doc_id={doc_id}.json"
    existing = get_json(s3, bucket, manifest_key)
    if existing and existing.get("status") == "ok":
        return {"doc_id": doc_id, "status": "skipped_existing", "facts": 0}

    payload = fetch_csv_package(api_key, doc_id)
    package_hash = sha256_bytes(payload)
    submit_date = str(doc.get("submitDateTime") or "unknown")[:10] or "unknown"
    category = doc["_category"]
    doc_type = str(doc.get("docTypeCode") or "unknown")

    raw_key = (
        f"raw/edinet/csv_packages/category={category}/doc_type={doc_type}/"
        f"submit_date={submit_date}/{doc_id}_{package_hash}.zip"
    )
    s3.put_object(
        Bucket=bucket,
        Key=raw_key,
        Body=payload,
        ContentType="application/zip",
        Metadata={"sha256": package_hash, "docid": doc_id},
    )

    facts = normalized_facts_from_zip(payload, doc, observed_dt, run_id)
    normalized_key = (
        f"normalized/edinet/xbrl_facts/category={category}/doc_type={doc_type}/"
        f"submit_date={submit_date}/{doc_id}.parquet"
    )
    put_parquet(s3, bucket, normalized_key, facts)

    manifest = {
        "status": "ok",
        "source_id": "edinet_api_v2",
        "dataset": "xbrl_csv_facts",
        "doc_id": doc_id,
        "doc_type_code": doc_type,
        "category": category,
        "doc_description": doc.get("docDescription"),
        "edinet_code": doc.get("edinetCode"),
        "sec_code": doc.get("secCode"),
        "stock_code": str(doc.get("secCode") or "")[:4] or None,
        "filer_name": doc.get("filerName"),
        "issuer_edinet_code": doc.get("issuerEdinetCode"),
        "subject_edinet_code": doc.get("subjectEdinetCode"),
        "parent_doc_id": doc.get("parentDocID"),
        "submit_datetime": doc.get("submitDateTime"),
        "known_at": known_at_for_document(doc, observed_dt),
        "observed_at": utc_iso(observed_dt),
        "package_sha256": package_hash,
        "raw_key": raw_key,
        "normalized_key": normalized_key,
        "fact_rows": len(facts),
        "ingestion_run_id": run_id,
    }
    put_json(s3, bucket, manifest_key, manifest)
    return {"doc_id": doc_id, "status": "ok", "facts": len(facts), "category": category}


def parse_args():
    parser = argparse.ArgumentParser(
        description="Download selected EDINET XBRL-to-CSV packages and normalize facts."
    )
    parser.add_argument(
        "--lookback-days",
        type=int,
        default=int(env("EDINET_PACKAGE_LOOKBACK_DAYS", "7")),
    )
    parser.add_argument(
        "--max-new-documents",
        type=int,
        default=int(env("EDINET_MAX_NEW_DOCUMENTS", "150")),
    )
    parser.add_argument(
        "--config",
        default=env("EDINET_DOCUMENT_TYPES_CONFIG", "config/edinet_document_types.json"),
    )
    return parser.parse_args()


def main():
    args = parse_args()
    if not 1 <= args.lookback_days <= 31:
        raise ValueError("--lookback-days must be between 1 and 31")
    if not 1 <= args.max_new_documents <= 500:
        raise ValueError("--max-new-documents must be between 1 and 500")

    api_key = env("EDINET_API_KEY")
    interval_seconds = float(env("EDINET_REQUEST_INTERVAL_SECONDS", "1.5"))
    if interval_seconds < 0.5:
        raise ValueError("EDINET_REQUEST_INTERVAL_SECONDS must be >= 0.5")

    _, rules_by_code = load_rules(Path(args.config))
    s3 = b2_client()
    bucket = env("B2_BUCKET_NAME")
    observed_dt = datetime.now(timezone.utc)
    run_id = f"{observed_dt:%Y%m%dT%H%M%SZ}-{uuid.uuid4().hex[:8]}"
    today_jst = observed_dt.astimezone(JST).date()

    candidates = []
    list_counts = {}
    for offset in range(args.lookback_days - 1, -1, -1):
        target_date = today_jst - timedelta(days=offset)
        body = fetch_list(api_key, target_date)
        selected = select_documents(body, rules_by_code)
        list_counts[target_date.isoformat()] = len(selected)
        candidates.extend(selected)
        time.sleep(min(interval_seconds, 2.0))

    # Deduplicate because EDINET file-date rules can surface metadata operations for older submissions.
    unique = {}
    for doc in candidates:
        unique[str(doc["docID"])] = doc
    candidates = sorted(
        unique.values(),
        key=lambda d: (
            int(d["_priority"]),
            str(d.get("submitDateTime") or ""),
            str(d.get("docID") or ""),
        ),
    )

    results = []
    new_processed = 0
    for doc in candidates:
        if new_processed >= args.max_new_documents:
            break
        doc_id = str(doc["docID"])
        existing = get_json(s3, bucket, f"metadata/edinet/packages/doc_id={doc_id}.json")
        if existing and existing.get("status") == "ok":
            results.append({"doc_id": doc_id, "status": "skipped_existing", "facts": 0})
            continue

        try:
            result = process_document(
                s3=s3,
                bucket=bucket,
                api_key=api_key,
                doc=doc,
                observed_dt=observed_dt,
                run_id=run_id,
            )
            results.append(result)
            new_processed += 1
        except Exception as exc:
            error_manifest = {
                "status": "error",
                "doc_id": doc_id,
                "doc_type_code": doc.get("docTypeCode"),
                "category": doc.get("_category"),
                "observed_at": utc_iso(observed_dt),
                "error": str(exc)[:1000],
                "ingestion_run_id": run_id,
            }
            put_json(
                s3,
                bucket,
                f"metadata/edinet/package_errors/doc_id={doc_id}/{run_id}.json",
                error_manifest,
            )
            results.append({"doc_id": doc_id, "status": "error", "error": str(exc)[:300]})
        time.sleep(interval_seconds)

    status_counts = {}
    for item in results:
        status_counts[item["status"]] = status_counts.get(item["status"], 0) + 1

    manifest = {
        "source_id": "edinet_api_v2",
        "dataset": "xbrl_csv_facts",
        "run_id": run_id,
        "observed_at": utc_iso(observed_dt),
        "lookback_days": args.lookback_days,
        "max_new_documents": args.max_new_documents,
        "candidate_documents": len(candidates),
        "selected_by_file_date": list_counts,
        "status_counts": status_counts,
        "new_fact_rows": sum(int(item.get("facts") or 0) for item in results if item["status"] == "ok"),
        "results": results,
    }
    now = observed_dt.astimezone(timezone.utc)
    manifest_key = (
        f"metadata/edinet/package_runs/year={now:%Y}/month={now:%m}/day={now:%d}/"
        f"{run_id}.json"
    )
    put_json(s3, bucket, manifest_key, manifest)
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise
