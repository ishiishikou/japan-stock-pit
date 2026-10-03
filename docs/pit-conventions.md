# Point-in-time conventions

The goal is to reproduce what could have been known at a historical decision time without look-ahead bias.

## Required normalized fields

| Field | Meaning |
|---|---|
| `source_id` | Stable source identifier, e.g. `boj_timeseries`. |
| `dataset` | Logical dataset name. |
| `series_id` | Source-native series/document/record identifier. |
| `period_code` | Source-native observation period. Preserve exactly as published. |
| `value` | Parsed value when numeric; null when unavailable. |
| `frequency` | DAILY, MONTHLY, QUARTERLY, etc. |
| `unit` | Source-provided unit. |
| `observed_at` | UTC timestamp when our collector actually fetched the source. |
| `known_at` | Conservative timestamp from which a backtest may use the value. |
| `source_last_update` | Provider-supplied update date/time when available. |
| `source_url` | API/page/document URL used for retrieval. |
| `payload_sha256` | Hash of the source payload or source response. |
| `ingestion_run_id` | Unique collector run identifier. |

## known_at rule

1. If the provider exposes an exact publication timestamp, use it.
2. If only a publication/update **date** is available, use **23:59:59 JST on that date** and convert it to UTC. This is intentionally conservative and avoids granting the backtest information earlier than we can prove it was available.
3. If no provider timestamp/date exists, use `observed_at`.
4. Never backfill `known_at` from a later-discovered historical value.

## Revisions

A historical value may be revised after first publication. The normalized change log therefore keeps every newly observed or revised value. The current snapshot is convenient for queries, but the change log is the source of truth for PIT reconstruction.

## Raw retention

Raw payloads are stored only when the content hash changes. This preserves source evidence while avoiding daily duplication of identical responses.
