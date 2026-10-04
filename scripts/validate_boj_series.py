#!/usr/bin/env python3
"""Validate configured BOJ series directly against the official API."""

import json
from datetime import date
from pathlib import Path

import requests


CONFIG = "config/boj_macro_series.json"


def main():
    cfg = json.loads(Path(CONFIG).read_text(encoding="utf-8"))
    # A recent two-year window is enough to validate active series without
    # downloading their full research history.
    # YYYY01 is valid as January for monthly/daily series and Q1 for
    # quarterly series, letting one validation window cover mixed frequencies.
    start_date = f"{date.today().year - 2}01"

    results = []
    failed = []
    for spec in cfg["series"]:
        params = {
            "format": "json",
            "lang": cfg.get("language", "en"),
            "db": spec["db"],
            "code": spec["code"],
            "startDate": start_date,
        }
        response = requests.get(
            cfg["endpoint"],
            params=params,
            timeout=60,
            headers={"User-Agent": "japan-stock-pit/1.0"},
        )
        response.raise_for_status()
        body = response.json()
        status = int(body.get("STATUS", 0))
        resultset = body.get("RESULTSET") or []

        rows = 0
        returned_codes = []
        for item in resultset:
            returned_codes.append(str(item.get("SERIES_CODE") or ""))
            values = item.get("VALUES") or {}
            rows += len(values.get("SURVEY_DATES") or [])

        ok = (
            status == 200
            and bool(resultset)
            and rows > 0
            and spec["code"] in returned_codes
        )
        result = {
            "dataset": spec["dataset"],
            "db": spec["db"],
            "code": spec["code"],
            "status": status,
            "returned_codes": returned_codes,
            "recent_rows": rows,
            "ok": ok,
        }
        results.append(result)
        if not ok:
            failed.append(result)

    print(json.dumps({"validation_start_date": start_date, "results": results}, indent=2))
    if failed:
        raise RuntimeError(
            "Invalid or empty BOJ series: "
            + ", ".join(f"{x['db']}:{x['code']}" for x in failed)
        )


if __name__ == "__main__":
    main()
