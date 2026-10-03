# B2 storage monitoring

The project uses a 10 GiB reference budget to keep the free-data archive bounded.

A daily GitHub Actions workflow scans B2 object metadata and writes:

- `metadata/storage/latest.json`
- `metadata/storage/history/year=YYYY/month=MM/day=DD/*.json`

Thresholds are informational:

- below 70%: `ok`
- 70–85%: `watch`
- 85–95%: `warning`
- 95%+: `critical`

This is a project-side reference budget, not a billing statement from Backblaze. Large historical backfills should consult this report before expanding coverage.
