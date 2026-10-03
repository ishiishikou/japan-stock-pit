#!/usr/bin/env python3
import io
import json
import math
import os
import sys
import uuid
from datetime import datetime, timezone

import boto3
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
from botocore.config import Config
from botocore.exceptions import ClientError


SOURCE_PREFIX = "normalized/edinet/xbrl_facts/category=ownership/"
OUTPUT_PREFIX = "normalized/edinet/ownership_summary/"
MANIFEST_PREFIX = "metadata/edinet/ownership/"

DOC_ELEMENTS = {
    "document_title": ["DocumentTitleCoverPage"],
    "clause_of_stipulation": ["ClauseOfStipulationCoverPage"],
    "cover_name": ["NameCoverPage"],
    "filing_requirement_date": ["DateWhenFilingRequirementAroseCoverPage"],
    "filing_date": ["FilingDateCoverPage"],
    "total_filers_joint_holders": ["TotalNumberOfFilersAndJointHoldersCoverPage"],
    "arrangement_of_filing": ["ArrangementOfFilingCoverPage"],
    "change_reason": ["ReasonForFilingChangeReportCoverPage"],
    "issuer_name": ["NameOfIssuer"],
    "issuer_security_code": ["SecurityCodeOfIssuer"],
    "stock_listing": ["StockListing", "ListedOrOTC"],
}

HOLDER_ELEMENTS = {
    "holder_name": ["Name"],
    "purpose": ["PurposeOfHolding"],
    "important_proposal": ["ActOfMakingImportantProposalEtc"],
    "shares_held": ["TotalNumberOfStocksEtcHeld"],
    "potential_shares": ["NumberOfResidualStocksHeld"],
    "holding_ratio": ["HoldingRatioOfShareCertificatesEtc"],
    "previous_holding_ratio": ["HoldingRatioOfShareCertificatesEtcPerLastReport"],
}

OUTPUT_COLUMNS = [
    "source_id",
    "dataset",
    "doc_id",
    "doc_type_code",
    "doc_description",
    "document_title",
    "is_change_report",
    "is_correction",
    "parent_doc_id",
    "submit_datetime",
    "known_at",
    "observed_at",
    "filer_edinet_code",
    "filer_name",
    "issuer_edinet_code",
    "subject_edinet_code",
    "issuer_name",
    "issuer_security_code",
    "issuer_stock_code",
    "stock_listing",
    "filing_requirement_date",
    "filing_date",
    "clause_of_stipulation",
    "change_reason",
    "arrangement_of_filing",
    "total_filers_joint_holders",
    "holder_context_id",
    "holder_name",
    "purpose",
    "important_proposal",
    "shares_held_raw",
    "shares_held",
    "potential_shares_raw",
    "potential_shares",
    "holding_ratio_raw",
    "holding_ratio",
    "previous_holding_ratio_raw",
    "previous_holding_ratio",
    "holding_ratio_change",
    "evidence_element_ids",
    "source_parquet_key",
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


def read_parquet(s3, bucket: str, key: str) -> pd.DataFrame:
    body = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
    return pq.read_table(io.BytesIO(body)).to_pandas()


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


def list_parquet_keys(s3, bucket: str, prefix: str):
    token = None
    while True:
        kwargs = {"Bucket": bucket, "Prefix": prefix, "MaxKeys": 1000}
        if token:
            kwargs["ContinuationToken"] = token
        response = s3.list_objects_v2(**kwargs)
        for obj in response.get("Contents") or []:
            key = obj.get("Key") or ""
            if key.endswith(".parquet"):
                yield key
        if not response.get("IsTruncated"):
            break
        token = response.get("NextContinuationToken")


def local_name(element_id) -> str:
    text = str(element_id or "")
    if ":" in text:
        return text.rsplit(":", 1)[-1]
    return text


def nonempty(value):
    if value is None:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    text = str(value).strip()
    return text if text else None


def first_value(frame: pd.DataFrame, names):
    if frame.empty:
        return None
    subset = frame[frame["local_name"].isin(names)]
    for value in subset.get("value", pd.Series(dtype=object)).tolist():
        result = nonempty(value)
        if result is not None:
            return result
    return None


def parse_number(value):
    text = nonempty(value)
    if text is None:
        return None
    normalized = (
        text.replace(",", "")
        .replace("，", "")
        .replace(" ", "")
        .replace("　", "")
    )
    is_percent = normalized.endswith("%") or normalized.endswith("％")
    normalized = normalized.rstrip("%％")
    try:
        number = float(normalized)
    except ValueError:
        return None
    if is_percent:
        number /= 100.0
    return number


def safe_first(frame: pd.DataFrame, column: str):
    if column not in frame.columns or frame.empty:
        return None
    for value in frame[column].tolist():
        result = nonempty(value)
        if result is not None:
            return result
    return None


def infer_submit_date(frame: pd.DataFrame):
    submit = safe_first(frame, "submit_datetime")
    if submit:
        return submit[:10]
    known = safe_first(frame, "known_at")
    if known:
        return known[:10]
    return "unknown"


def build_rows(frame: pd.DataFrame, source_key: str, run_id: str):
    required = {"doc_id", "doc_type_code", "element_id", "context_id", "value"}
    missing = required - set(frame.columns)
    if missing:
        raise RuntimeError(f"Missing required ownership fact columns: {sorted(missing)}")

    frame = frame.copy()
    frame["local_name"] = frame["element_id"].map(local_name)

    doc_id = safe_first(frame, "doc_id")
    doc_type_code = safe_first(frame, "doc_type_code")
    doc_description = safe_first(frame, "doc_description")
    if not doc_id:
        raise RuntimeError(f"No doc_id in {source_key}")

    doc_values = {
        name: first_value(frame, aliases)
        for name, aliases in DOC_ELEMENTS.items()
    }

    metadata = {
        "source_id": "edinet_api_v2",
        "dataset": "ownership_summary",
        "doc_id": doc_id,
        "doc_type_code": doc_type_code,
        "doc_description": doc_description,
        "document_title": doc_values["document_title"],
        "is_change_report": "変更報告書" in str(
            doc_values["document_title"] or doc_description or ""
        ),
        "is_correction": str(doc_type_code) == "360"
        or "訂正" in str(doc_values["document_title"] or doc_description or ""),
        "parent_doc_id": safe_first(frame, "parent_doc_id"),
        "submit_datetime": safe_first(frame, "submit_datetime"),
        "known_at": safe_first(frame, "known_at"),
        "observed_at": safe_first(frame, "observed_at"),
        "filer_edinet_code": safe_first(frame, "edinet_code"),
        "filer_name": safe_first(frame, "filer_name"),
        "issuer_edinet_code": safe_first(frame, "issuer_edinet_code"),
        "subject_edinet_code": safe_first(frame, "subject_edinet_code"),
        "issuer_name": doc_values["issuer_name"],
        "issuer_security_code": doc_values["issuer_security_code"],
        "issuer_stock_code": (
            str(doc_values["issuer_security_code"])[:4]
            if doc_values["issuer_security_code"]
            else None
        ),
        "stock_listing": doc_values["stock_listing"],
        "filing_requirement_date": doc_values["filing_requirement_date"],
        "filing_date": doc_values["filing_date"],
        "clause_of_stipulation": doc_values["clause_of_stipulation"],
        "change_reason": doc_values["change_reason"],
        "arrangement_of_filing": doc_values["arrangement_of_filing"],
        "total_filers_joint_holders": parse_number(
            doc_values["total_filers_joint_holders"]
        ),
        "source_parquet_key": source_key,
        "ingestion_run_id": run_id,
    }

    holder_local_names = {
        alias
        for aliases in HOLDER_ELEMENTS.values()
        for alias in aliases
    }
    candidate = frame[frame["local_name"].isin(holder_local_names)].copy()

    rows = []
    for context_id, group in candidate.groupby("context_id", dropna=False):
        context_text = nonempty(context_id)
        values = {
            field: first_value(group, aliases)
            for field, aliases in HOLDER_ELEMENTS.items()
        }
        if not any(nonempty(value) for value in values.values()):
            continue

        # Avoid abstract/document-level contexts that contain only a label-like name.
        substantial = any(
            nonempty(values[field])
            for field in (
                "purpose",
                "shares_held",
                "potential_shares",
                "holding_ratio",
                "previous_holding_ratio",
            )
        )
        if not substantial:
            continue

        ratio = parse_number(values["holding_ratio"])
        previous_ratio = parse_number(values["previous_holding_ratio"])
        evidence = sorted(
            {
                str(value)
                for value in group["element_id"].tolist()
                if nonempty(value) is not None
            }
        )
        row = {
            **metadata,
            "holder_context_id": context_text,
            "holder_name": values["holder_name"],
            "purpose": values["purpose"],
            "important_proposal": values["important_proposal"],
            "shares_held_raw": values["shares_held"],
            "shares_held": parse_number(values["shares_held"]),
            "potential_shares_raw": values["potential_shares"],
            "potential_shares": parse_number(values["potential_shares"]),
            "holding_ratio_raw": values["holding_ratio"],
            "holding_ratio": ratio,
            "previous_holding_ratio_raw": values["previous_holding_ratio"],
            "previous_holding_ratio": previous_ratio,
            "holding_ratio_change": (
                ratio - previous_ratio
                if ratio is not None and previous_ratio is not None
                else None
            ),
            "evidence_element_ids": json.dumps(evidence, ensure_ascii=False),
        }
        rows.append(row)

    if not rows:
        # Keep document-level coverage even if holder-context mapping is not yet understood.
        rows.append(
            {
                **metadata,
                "holder_context_id": None,
                "holder_name": None,
                "purpose": None,
                "important_proposal": None,
                "shares_held_raw": None,
                "shares_held": None,
                "potential_shares_raw": None,
                "potential_shares": None,
                "holding_ratio_raw": None,
                "holding_ratio": None,
                "previous_holding_ratio_raw": None,
                "previous_holding_ratio": None,
                "holding_ratio_change": None,
                "evidence_element_ids": "[]",
            }
        )

    return pd.DataFrame(rows, columns=OUTPUT_COLUMNS)


def main():
    s3 = b2_client()
    bucket = env("B2_BUCKET_NAME")
    now = datetime.now(timezone.utc)
    run_id = f"{now:%Y%m%dT%H%M%SZ}-{uuid.uuid4().hex[:8]}"

    processed = 0
    skipped = 0
    failed = 0
    output_rows = 0
    failures = []

    for source_key in list_parquet_keys(s3, bucket, SOURCE_PREFIX):
        doc_id = source_key.rsplit("/", 1)[-1].removesuffix(".parquet")
        manifest_key = f"{MANIFEST_PREFIX}doc_id={doc_id}.json"
        existing = get_json(s3, bucket, manifest_key)
        if existing and existing.get("status") == "ok":
            skipped += 1
            continue

        try:
            frame = read_parquet(s3, bucket, source_key)
            summary = build_rows(frame, source_key, run_id)
            submit_date = infer_submit_date(frame)
            output_key = (
                f"{OUTPUT_PREFIX}submit_date={submit_date}/{doc_id}.parquet"
            )
            put_parquet(s3, bucket, output_key, summary)

            manifest = {
                "status": "ok",
                "source_id": "edinet_api_v2",
                "dataset": "ownership_summary",
                "doc_id": doc_id,
                "source_key": source_key,
                "output_key": output_key,
                "row_count": len(summary),
                "observed_at": now.isoformat().replace("+00:00", "Z"),
                "ingestion_run_id": run_id,
            }
            put_json(s3, bucket, manifest_key, manifest)
            processed += 1
            output_rows += len(summary)
        except Exception as exc:
            failed += 1
            failures.append({"doc_id": doc_id, "error": str(exc)[:500]})
            put_json(
                s3,
                bucket,
                f"{MANIFEST_PREFIX}errors/doc_id={doc_id}/{run_id}.json",
                {
                    "status": "error",
                    "doc_id": doc_id,
                    "source_key": source_key,
                    "error": str(exc)[:1000],
                    "ingestion_run_id": run_id,
                },
            )

    run_manifest = {
        "source_id": "edinet_api_v2",
        "dataset": "ownership_summary",
        "run_id": run_id,
        "observed_at": now.isoformat().replace("+00:00", "Z"),
        "processed_documents": processed,
        "skipped_documents": skipped,
        "failed_documents": failed,
        "output_rows": output_rows,
        "failures": failures,
    }
    put_json(
        s3,
        bucket,
        (
            f"metadata/edinet/ownership_runs/year={now:%Y}/month={now:%m}/"
            f"day={now:%d}/{run_id}.json"
        ),
        run_manifest,
    )
    print(json.dumps(run_manifest, ensure_ascii=False, indent=2))

    if failed and not processed:
        raise RuntimeError("All unprocessed ownership documents failed normalization")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise
