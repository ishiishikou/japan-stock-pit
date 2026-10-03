# EDINET financial metric coverage

Before deriving ROE, ROIC, FCF or valuation factors, the project measures how
well the current canonical metric aliases actually cover real EDINET filings.

The daily coverage report records:

- rows per canonical metric
- distinct documents per metric
- distinct stock codes per metric
- numeric parsing ratio
- document-type distribution
- relative-year distribution
- consolidated/non-consolidated labels
- period/instant labels
- units and common context IDs

Output:

`metadata/edinet/financial_metric_coverage/latest.json`

The report is used to expand taxonomy aliases from evidence rather than guess
at XBRL element names.
