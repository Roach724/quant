# Known Issues

Last updated: 2026-05-15 (after fix round 2 — all Phase 1 issues resolved)

## Fixed (Round 2)

### Issue 1: BigQuery SQL Analytics — FIXED

Used native BigQuery tables with LOAD DATA jobs instead of external tables (which don't support multi-level GCS globs). A Cloud Run Job (`quant-bq-loader`) runs daily at 6am ET, loading Parquet files via per-day single-glob patterns. Table is partitioned by `DATE(timestamp)` and clustered by `symbol`.

**Files:** `terraform/bigquery.tf`, `bigquery_loader/main.py`

### Issue 5: Go Bars Endpoint Returns Empty — FIXED

Instead of direct parquet decoding (which requires CGO-compatible libraries unavailable in `CGO_ENABLED=0` builds), the collector now writes a companion JSON file alongside each Parquet file. The Go API reads these JSON files to serve bar data. This avoids all parquet CGO dependency issues.

**Confirmed:** `GET /api/v1/bars?market=us&symbols=AAPL` returns 120 real OHLCV bars.

**Files:** `collectors/storage.py` (JSON companion), `query-api/internal/reader/reader.go` (JSON reader + market validation)

### Issue: Market Validation — FIXED

`ParseQueryParams` now validates market codes via `market.ParseMarket()`. Previously `?market=jp` would silently succeed; now returns 400.

**Files:** `query-api/internal/reader/reader.go`

---

## Fixed (Round 1)

- Issue 2: API auth — SDK auto-detects GCP credentials
- Issue 3: SDK direct GCS — `gcsfs` integration
- Issue 4: Symbol listing — scans GCS, returns real symbols
- Issue 6: Cloud Run memory floor — 512 MiB
- Issue 7: Terraform 1.9 inline blocks — multi-line
- Issue 8: Storage path format — Hive-style `year=/month=/day=/symbol=`

---

## Remaining Open

### Minor: VCR Test Cassettes Directory

`collectors/tests/cassettes/` — untracked directory with recorded API responses. Add to `.gitignore`.

---

## Phase 2 Priority

All Phase 1 issues resolved. Ready for Phase 2 (research engine + backtesting).
