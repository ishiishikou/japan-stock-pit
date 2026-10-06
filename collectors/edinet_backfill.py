#!/usr/bin/env python3
import json
import os
import time
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from botocore.exceptions import ClientError

from edinet_packages import (
    JST,
    b2_client,
    env,
    fetch_list,
    get_json,
    load_rules,
    list_successful_package_doc_ids,
    process_document,
    put_json,
    select_documents,
)


STATE_KEY = "metadata/edinet/backfill/state.json"
STORAGE_KEY = "metadata/storage/latest.json"

PHASES = [
    {"name": "ownership", "codes": {"350", "360"}},
    {"name": "financial_annual", "codes": {"120", "130"}},
    {"name": "financial_periodic", "codes": {"140", "150", "160", "170"}},
    {"name": "events_buyback", "codes": {"180", "190", "220"}},
    {
        "name": "tender_offer",
        "codes": {"240", "250", "270", "280", "290", "300", "310", "320"},
    },
]


def phase_index(name):
    for idx, phase in enumerate(PHASES):
        if phase["name"] == name:
            return idx
    return 0


def initial_state(today_jst):
    return {
        "version": 1,
        "phase": PHASES[0]["name"],
        "cursor_date": (today_jst - timedelta(days=7)).isoformat(),
        "min_date": (today_jst - timedelta(days=3650)).isoformat(),
        "completed_phases": [],
        "all_completed": False,
    }


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


def print_b2_cap_pause(run_id, observed_dt, state=None, phase=None, error=None):
    payload = {
        "status": "paused",
        "reason": "b2_download_or_class_b_cap_exceeded",
        "run_id": run_id,
        "observed_at": observed_dt.isoformat().replace("+00:00", "Z"),
        "phase": phase,
        "state": state,
        "error": str(error)[:500] if error else None,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def storage_allows_backfill(storage, phase_name):
    if not storage:
        return True, "no_storage_report"
    level = storage.get("level", "ok")
    if level in {"warning", "critical"}:
        return False, f"storage_{level}"
    if level == "watch" and phase_name != "ownership":
        return False, "storage_watch_nonownership_paused"
    return True, f"storage_{level}"


def filtered_candidates(body, rules_by_code, phase):
    selected = select_documents(body, rules_by_code)
    allowed = phase["codes"]
    docs = [d for d in selected if str(d.get("docTypeCode") or "") in allowed]
    dedup = {}
    for doc in docs:
        dedup[str(doc["docID"])] = doc
    return sorted(
        dedup.values(),
        key=lambda d: (
            int(d.get("_priority", 99)),
            str(d.get("submitDateTime") or ""),
            str(d.get("docID") or ""),
        ),
    )


def advance_phase(state, today_jst):
    current = state["phase"]
    completed = list(state.get("completed_phases") or [])
    if current not in completed:
        completed.append(current)
    idx = phase_index(current)
    if idx + 1 >= len(PHASES):
        state["completed_phases"] = completed
        state["all_completed"] = True
        return
    state["completed_phases"] = completed
    state["phase"] = PHASES[idx + 1]["name"]
    state["cursor_date"] = (today_jst - timedelta(days=7)).isoformat()


def main():
    api_key = env("EDINET_API_KEY")
    days_per_run = int(os.getenv("EDINET_BACKFILL_DAYS_PER_RUN", "14"))
    max_new_documents = int(os.getenv("EDINET_BACKFILL_MAX_NEW_DOCUMENTS", "75"))
    interval_seconds = float(os.getenv("EDINET_REQUEST_INTERVAL_SECONDS", "1.5"))
    if not 1 <= days_per_run <= 31:
        raise ValueError("EDINET_BACKFILL_DAYS_PER_RUN must be 1..31")
    if not 1 <= max_new_documents <= 300:
        raise ValueError("EDINET_BACKFILL_MAX_NEW_DOCUMENTS must be 1..300")
    if interval_seconds < 0.5:
        raise ValueError("EDINET_REQUEST_INTERVAL_SECONDS must be >= 0.5")

    _, rules_by_code = load_rules(
        Path(os.getenv("EDINET_DOCUMENT_TYPES_CONFIG", "config/edinet_document_types.json"))
    )
    s3 = b2_client()
    bucket = env("B2_BUCKET_NAME")
    observed_dt = datetime.now(timezone.utc)
    today_jst = observed_dt.astimezone(JST).date()
    run_id = f"{observed_dt:%Y%m%dT%H%M%SZ}-{uuid.uuid4().hex[:8]}"

    try:
        state = get_json(s3, bucket, STATE_KEY) or initial_state(today_jst)
    except ClientError as exc:
        if is_b2_cap_exceeded(exc):
            print_b2_cap_pause(run_id, observed_dt, error=exc)
            return
        raise

    if state.get("all_completed"):
        print(json.dumps({"status": "all_completed", "state": state}, ensure_ascii=False, indent=2))
        return

    current_phase = PHASES[phase_index(state.get("phase"))]
    state["phase"] = current_phase["name"]
    try:
        storage = get_json(s3, bucket, STORAGE_KEY)
    except ClientError as exc:
        if is_b2_cap_exceeded(exc):
            print_b2_cap_pause(
                run_id,
                observed_dt,
                state=state,
                phase=current_phase["name"],
                error=exc,
            )
            return
        raise

    allowed, guard_reason = storage_allows_backfill(storage, current_phase["name"])
    if not allowed:
        manifest = {
            "status": "paused",
            "reason": guard_reason,
            "run_id": run_id,
            "observed_at": observed_dt.isoformat().replace("+00:00", "Z"),
            "state": state,
            "storage": {
                "gib": storage.get("gib") if storage else None,
                "level": storage.get("level") if storage else None,
            },
        }
        put_json(
            s3,
            bucket,
            f"metadata/edinet/backfill/runs/year={observed_dt:%Y}/month={observed_dt:%m}/day={observed_dt:%d}/{run_id}.json",
            manifest,
        )
        print(json.dumps(manifest, ensure_ascii=False, indent=2))
        return

    try:
        processed_doc_ids = list_successful_package_doc_ids(s3, bucket)
    except ClientError as exc:
        if is_b2_cap_exceeded(exc):
            print_b2_cap_pause(
                run_id,
                observed_dt,
                state=state,
                phase=current_phase["name"],
                error=exc,
            )
            return
        raise

    cursor = date.fromisoformat(state["cursor_date"])
    min_date = date.fromisoformat(state["min_date"])
    dates_completed = 0
    processed = 0
    skipped = 0
    failed = 0
    selected_total = 0
    results = []

    while dates_completed < days_per_run and not state.get("all_completed"):
        if cursor < min_date:
            advance_phase(state, today_jst)
            if state.get("all_completed"):
                break
            current_phase = PHASES[phase_index(state["phase"])]
            cursor = date.fromisoformat(state["cursor_date"])
            continue

        body = fetch_list(api_key, cursor)
        candidates = filtered_candidates(body, rules_by_code, current_phase)
        selected_total += len(candidates)
        date_result = {
            "date": cursor.isoformat(),
            "phase": current_phase["name"],
            "selected": len(candidates),
            "processed": 0,
            "skipped": 0,
            "failed": 0,
            "complete": True,
        }

        for doc in candidates:
            doc_id = str(doc["docID"])
            if doc_id in processed_doc_ids:
                skipped += 1
                date_result["skipped"] += 1
                continue

            if processed >= max_new_documents:
                date_result["complete"] = False
                break

            try:
                outcome = process_document(
                    s3=s3,
                    bucket=bucket,
                    api_key=api_key,
                    doc=doc,
                    observed_dt=observed_dt,
                    run_id=run_id,
                )
                processed += 1
                date_result["processed"] += 1
                processed_doc_ids.add(doc_id)
                results.append(outcome)
            except ClientError as exc:
                if is_b2_cap_exceeded(exc):
                    print_b2_cap_pause(
                        run_id,
                        observed_dt,
                        state=state,
                        phase=current_phase["name"],
                        error=exc,
                    )
                    return
                failed += 1
                date_result["failed"] += 1
                results.append(
                    {
                        "doc_id": doc_id,
                        "status": "error",
                        "date": cursor.isoformat(),
                        "phase": current_phase["name"],
                        "error": str(exc)[:500],
                    }
                )
            except Exception as exc:
                failed += 1
                date_result["failed"] += 1
                results.append(
                    {
                        "doc_id": doc_id,
                        "status": "error",
                        "date": cursor.isoformat(),
                        "phase": current_phase["name"],
                        "error": str(exc)[:500],
                    }
                )
            time.sleep(interval_seconds)

        if not date_result["complete"]:
            results.append(date_result)
            state["cursor_date"] = cursor.isoformat()
            put_json(s3, bucket, STATE_KEY, state)
            break

        results.append(date_result)
        dates_completed += 1
        cursor -= timedelta(days=1)
        state["cursor_date"] = cursor.isoformat()
        put_json(s3, bucket, STATE_KEY, state)
        time.sleep(min(interval_seconds, 2.0))

    manifest = {
        "status": "ok",
        "source_id": "edinet_api_v2",
        "dataset": "historical_package_backfill",
        "run_id": run_id,
        "observed_at": observed_dt.isoformat().replace("+00:00", "Z"),
        "phase": current_phase["name"],
        "guard_reason": guard_reason,
        "dates_completed": dates_completed,
        "selected_documents": selected_total,
        "processed_documents": processed,
        "skipped_documents": skipped,
        "failed_documents": failed,
        "state": state,
        "results": results,
    }
    put_json(
        s3,
        bucket,
        f"metadata/edinet/backfill/runs/year={observed_dt:%Y}/month={observed_dt:%m}/day={observed_dt:%d}/{run_id}.json",
        manifest,
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
