# EDINET canonical financial metrics

The generic XBRL fact layer is intentionally broad. This processor adds a conservative derived layer that maps **exact XBRL local names** to a small canonical metric vocabulary.

It does not try to choose a single "correct" annual value yet. Instead it preserves context, relative year, consolidation flag, period/instant, units, raw value, and parsed numeric value.

That design avoids hiding ambiguity and keeps PIT research reproducible.

## Initial canonical metrics

Revenue, operating income, ordinary income, pretax income, net income, assets, liabilities, net assets, shareholders' equity, cash, interest-bearing debt, operating/investing/financing cash flow, depreciation, capital expenditure, R&D, EPS, dividend per share, and shares outstanding.

Aliases live in `config/financial_metric_elements.json` and are designed to be expanded transparently as real EDINET documents reveal additional taxonomy variants.

## Storage

```text
normalized/edinet/financial_metrics/
  submit_date=YYYY-MM-DD/<docID>.parquet

metadata/edinet/financial_metrics/
  doc_id=<docID>.json
```

The generic `normalized/edinet/xbrl_facts/` layer remains the source of truth.
