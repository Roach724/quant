# Progress Log

## Session: 2026-05-17

### Discovery Phase (completed)
- Analyzed full data pipeline write patterns across all markets
- Identified systematic intraday data loss from overwrite + lookback window
- Discovered HK daily/minute collision on same GCS file path
- Confirmed no dedup logic exists anywhere in the codebase
- Mapped all code paths requiring changes (Python, Go, Terraform)

### Design Phase (completed)
- Selected Plan B: WRITE_APPEND + BQ dedup on `_ingest_time`
- GCS path: `raw/{market}/bars/freq={freq}/year=.../.../symbol={S}.parquet`
- Frequency spec: all 3 markets = 5m + 1d
- 6 collectors, 6 BQ loaders, 6 BQ tables — one per (market, frequency)
- Clean-slate strategy: destroy existing, rebuild from scratch
- Planning files written to `superpowers/`

### Implementation Phase (pending)
- Phase 0: Destroy existing resources
- Phase 1: Core storage layer (storage.py, main.py, backfill.py)
- Phase 2: BigQuery loader (WRITE_APPEND + dedup)
- Phase 3: Python SDK (direct.py, __init__.py)
- Phase 4: Go query API (reader.go, handler.go)
- Phase 5: Terraform (6 collectors + 6 loaders + 6 tables)
- Phase 6: Deploy & verify

### Status
Waiting for user approval to begin implementation.
