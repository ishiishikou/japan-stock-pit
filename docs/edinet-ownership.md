# EDINET ownership normalization

This processor converts the generic EDINET XBRL-to-CSV fact layer for document types 350/360 into a research-friendly ownership table.

## Document classification

- `350`: large-shareholding reports, including change reports
- `360`: corrections to large-shareholding reports

A change report does not have a separate `docTypeCode`; it is identified from the filing title/description.

## Extracted fields

Document-level:

- filer EDINET code / name
- issuer name / security code
- filing requirement date
- filing date / submit timestamp
- filing clause
- change reason
- correction / change-report flags
- parent document ID
- PIT `known_at`

Holder-context level:

- holder name
- purpose of holding
- important-proposal text
- shares held
- potential shares
- current holding ratio
- previous holding ratio
- computed ratio change
- evidence element IDs

The original raw values are retained next to parsed numeric values.

## Storage

```text
normalized/edinet/ownership_summary/
  submit_date=YYYY-MM-DD/<docID>.parquet

metadata/edinet/ownership/
  doc_id=<docID>.json
  errors/
```

The processor is evidence-preserving: the generic fact table remains the source of truth, and the ownership summary is a derived layer that can be regenerated as extraction logic improves.
