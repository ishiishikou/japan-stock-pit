"""Canonical cross-source security identifier mapping."""

from __future__ import annotations

import pandas as pd


OUTPUT_COLUMNS = [
    "ticker",
    "sec_code",
    "jquants_code",
    "edinet_code",
    "company_name",
    "edinet_filer_name",
    "jquants_company_name",
    "known_at",
    "observed_at",
    "code_match",
]


def clean_text(value):
    if value is None or pd.isna(value):
        return None
    text = str(value).strip()
    if text.endswith(".0"):
        text = text[:-2]
    return text or None


def normalize_security_code(value):
    """Normalize EDINET/J-Quants 5-character code to the 4-character ticker.

    Newer JPX security codes may contain letters, so normalization deliberately
    does not require an all-numeric code.
    """
    text = clean_text(value)
    if text is None:
        return None
    text = text.upper()
    if len(text) == 5 and text.endswith("0"):
        return text[:4]
    if len(text) == 4:
        return text
    return None


def _latest_by_ticker(frame, ticker_col):
    if frame.empty:
        return frame.copy()
    work = frame.copy()
    work["_ticker"] = work[ticker_col].map(normalize_security_code)
    work["_known"] = pd.to_datetime(work["known_at"], utc=True, errors="coerce")
    work["_observed"] = pd.to_datetime(work["observed_at"], utc=True, errors="coerce")
    work = work.loc[
        work["_ticker"].notna() & work["_known"].notna() & work["_observed"].notna()
    ].copy()
    work = work.sort_values(
        ["_ticker", "_known", "_observed"],
        kind="stable",
    )
    return work.groupby("_ticker", sort=False, as_index=False).tail(1)


def build_identifier_map(edinet: pd.DataFrame, jquants_master: pd.DataFrame) -> pd.DataFrame:
    """Build a conservative latest cross-source identifier map.

    The combined row's known_at and observed_at are the later timestamps from
    the contributing sources. This prevents the joined mapping from appearing
    available before both contributing records were available.

    This table is for cross-source joins. Historical listing/universe validity
    remains a separate survivorship/delisting concern.
    """
    edinet_required = {
        "sec_code",
        "edinet_code",
        "filer_name",
        "known_at",
        "observed_at",
    }
    jquants_required = {
        "Code",
        "CoName",
        "known_at",
        "observed_at",
    }
    missing_edinet = sorted(edinet_required - set(edinet.columns))
    missing_jquants = sorted(jquants_required - set(jquants_master.columns))
    if missing_edinet:
        raise ValueError(f"missing EDINET identifier columns: {missing_edinet}")
    if missing_jquants:
        raise ValueError(f"missing J-Quants identifier columns: {missing_jquants}")

    e = _latest_by_ticker(edinet, "sec_code")
    q = _latest_by_ticker(jquants_master, "Code")

    e = e[
        ["_ticker", "sec_code", "edinet_code", "filer_name", "known_at", "observed_at"]
    ].rename(
        columns={
            "known_at": "edinet_known_at",
            "observed_at": "edinet_observed_at",
        }
    )
    q = q[
        ["_ticker", "Code", "CoName", "known_at", "observed_at"]
    ].rename(
        columns={
            "Code": "jquants_code",
            "CoName": "jquants_company_name",
            "known_at": "jquants_known_at",
            "observed_at": "jquants_observed_at",
        }
    )

    merged = e.merge(q, on="_ticker", how="outer")
    rows = []
    for _, item in merged.iterrows():
        ticker = clean_text(item.get("_ticker"))
        if ticker is None:
            continue

        sec_code = clean_text(item.get("sec_code"))
        jq_code = clean_text(item.get("jquants_code"))
        e_known = pd.to_datetime(item.get("edinet_known_at"), utc=True, errors="coerce")
        q_known = pd.to_datetime(item.get("jquants_known_at"), utc=True, errors="coerce")
        e_observed = pd.to_datetime(
            item.get("edinet_observed_at"), utc=True, errors="coerce"
        )
        q_observed = pd.to_datetime(
            item.get("jquants_observed_at"), utc=True, errors="coerce"
        )

        known_values = [v for v in (e_known, q_known) if pd.notna(v)]
        observed_values = [v for v in (e_observed, q_observed) if pd.notna(v)]
        if not known_values or not observed_values:
            continue

        edinet_name = clean_text(item.get("filer_name"))
        jq_name = clean_text(item.get("jquants_company_name"))
        code_match = None
        if sec_code is not None and jq_code is not None:
            code_match = sec_code.upper() == jq_code.upper()

        rows.append(
            {
                "ticker": ticker,
                "sec_code": sec_code,
                "jquants_code": jq_code,
                "edinet_code": clean_text(item.get("edinet_code")),
                "company_name": jq_name or edinet_name,
                "edinet_filer_name": edinet_name,
                "jquants_company_name": jq_name,
                "known_at": max(known_values).isoformat().replace("+00:00", "Z"),
                "observed_at": max(observed_values).isoformat().replace("+00:00", "Z"),
                "code_match": code_match,
            }
        )

    return pd.DataFrame(rows, columns=OUTPUT_COLUMNS).sort_values(
        "ticker", kind="stable"
    ).reset_index(drop=True)
