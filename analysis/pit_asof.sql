-- Point-in-time reconstruction pattern.
-- The rule is always: only facts with known_at <= decision time may be visible.

-- Example: latest canonical financial fact known for each ticker/metric/context
-- at a historical decision timestamp.
WITH eligible AS (
    SELECT *
    FROM financial_metrics
    WHERE CAST(known_at AS TIMESTAMP) <= TIMESTAMP '2026-01-31 06:00:00'
),
ranked AS (
    SELECT
        *,
        row_number() OVER (
            PARTITION BY stock_code, metric, context_id, relative_year, consolidation
            ORDER BY CAST(known_at AS TIMESTAMP) DESC,
                     CAST(observed_at AS TIMESTAMP) DESC,
                     doc_id DESC
        ) AS rn
    FROM eligible
)
SELECT *
FROM ranked
WHERE rn = 1;

-- Ownership changes known by the same decision time.
SELECT *
FROM ownership_summary
WHERE CAST(known_at AS TIMESTAMP) <= TIMESTAMP '2026-01-31 06:00:00'
  AND holding_ratio_change IS NOT NULL
ORDER BY known_at, issuer_stock_code;
