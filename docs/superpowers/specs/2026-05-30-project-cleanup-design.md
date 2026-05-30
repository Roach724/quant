# Project Cleanup — Cloud Run & GCS Read Removal

> Date: 2026-05-30 · Branch: `feature/quant-next-phase`

## Context

- Data collection has moved from Cloud Run Jobs to VM cron + ws_collector systemd daemon
- Project policy: all data queries go through BigQuery; GCS is write-only archive
- The Go query-api, Python SDK (direct GCS reads), and Cloud Run infra are no longer needed

## Deletions

### `query-api/` — entire Go service
- Reads bar data from GCS JSON companion files (violates BQ-only policy)
- PaperRunner already queries BigQuery directly — no consumers remain
- Cloud Run deployment (`terraform/cloud_run_api.tf`) will be marked deprecated

### `sdk/` — entire Python SDK
- `sdk/quant/direct.py` reads parquet directly from GCS via gcsfs
- `sdk/quant/client.py` calls the Go query-api (indirect GCS read)
- No remaining consumers; PaperRunner and all other modules query BQ directly

### Terraform — Cloud Run Jobs
- `terraform/cloud_run_jobs.tf` — already fully commented as DEPRECATED, remove file

### Docker — Cloud Run images
- `collectors/Dockerfile` — Cloud Run Job image, no longer deployed
- `docker/Dockerfile.collector` — alternative Docker approach, never adopted

## Modifications

### `collectors/storage.py`
- Remove JSON companion file write (~10 lines in `write_bars_to_gcs`)
- Keep parquet write as-is (GCS write for archive → BQ loading)

### `quality/main.py`
- Rewrite to query BigQuery instead of reading GCS parquet
- Use `google.cloud.bigquery` client, query `quant.{market}_bars_{freq}` tables
- Keep same quality check functions (completeness, freshness, sanity)
- Accept env vars: `MARKET`, `FREQUENCY`, `MAX_AGE_HOURS`

### `terraform/cloud_run_api.tf`
- Comment out all resources, add DEPRECATED header (same treatment as cloud_run_jobs.tf)

### `CLAUDE.md`
- Remove sections: SDK install/usage, query-api build/deploy, Cloud Run job deploy
- Remove Go-related commands (go vet, go test)
- Update architecture diagram: remove Go Query API box
- Update data flow: remove GCS→Go API→SDK path

### `MEMO.md`
- Remove query-api from VM services table
- Remove SDK from project structure table
- Update data architecture diagram
- Update "Cloud Run" status

## Preserved (no changes)

| Module | Reason |
|--------|--------|
| `collectors/adapters/yfinance*.py, akshare*.py, alpaca_adapter.py, crypto_binance_adapter.py` | Kept as fallback per user decision |
| `bigquery_loader/` | Still active on VM cron (GCS→BQ), Dockerfile optional |
| `collectors/main.py` | Still active on VM cron (1d Futu collection) |
| `collectors/ws_collector.py` | Active systemd daemon (5m WebSocket) |
| `collectors/backfill.py` | Active historical backfill tool |

## Dependency Check

Before finalizing SDK deletion, verify no remaining consumers:
- `run_paper.py` — references `source="sdk"` in docstring but uses `source="bq"` internally
- No other files import `quant.data` or `quant.client`

## Test Impact

- `sdk/tests/` — 3 tests deleted with SDK
- `query-api/` — Go tests deleted with service
- Other modules not affected
