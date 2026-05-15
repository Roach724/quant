# Known Issues

Last updated: 2026-05-15 (after fix round 1)

## Fixed

### 2. Query API Public Access Blocked by Org Policy — FIXED

`allUsers` IAM binding on Cloud Run is rejected by the GCP organization policy.
**Fix:** SDK client (`sdk/quant/client.py`) now auto-detects GCP credentials via `google.auth.default()` and attaches Bearer token to API requests. No manual token needed.

### 3. Python SDK — Direct GCS Mode — FIXED

**Fix:** `sdk/quant/direct.py` now supports GCS via `gcsfs`. Set `QUANT_GCS_BUCKET` env var or pass `base_path="bucket-name"`. Added `gcsfs` and `google-auth` to SDK dependencies.

### 4. Go Query API — Symbol Listing — FIXED

**Fix:** `GET /api/v1/symbols` now scans GCS listing and returns real symbols. Confirmed returning `["AAPL","AMZN","GOOGL","META","MSFT","NVDA","SPY","TSLA"]`.

### 6. Cloud Run Memory Floor — FIXED (deployment)

### 7. Terraform 1.9 Inline Block Syntax — FIXED (deployment)

### 8. Storage Path Format — FIXED

Changed from bare `YYYY/MM/DD/SYMBOL.parquet` to Hive-style `year=YYYY/month=MM/day=DD/symbol=SYMBOL.parquet`. Enables BigQuery compatibility (see Issue 1 below).

---

## Still Open

### 1. BigQuery External Table — Multi-Level Glob Not Supported

**Status:** Deferred (`terraform/bigquery.tf.defer`)

BigQuery external tables do not support multi-level glob patterns (`*/*/*/*.parquet`). This is a BigQuery limitation — source URIs with multiple asterisks are rejected at the API level.

**Fix options:**
- Flatten Parquet storage to single directory level: `raw/us/bars/symbol=AAPL-year=2026-month=05-day=15.parquet`
- Use BigQuery `LOAD DATA` jobs to copy Parquet into native tables (scheduled Cloud Function)
- Use `bq load` CLI in a Cloud Run Job to load data periodically

**Files:** `terraform/bigquery.tf.defer`, `collectors/storage.py`

### 5. Go Query API — Bars Endpoint (Parquet Decoding)

**Status:** Partial fix

`GET /api/v1/bars` now scans GCS and reports how many matching parquet objects were found, but does not decode parquet and return actual bar data. Decoding parquet in Go requires `segmentio/parquet-go` which has complex CGO-free build requirements.

**Paths that work today:**
- Python SDK `source="direct"` with GCS: reads parquet from GCS → pandas DataFrame ✅
- Go API `/api/v1/symbols`: scans GCS, returns real symbol list ✅
- Go API `/api/v1/bars`: finds matching objects but returns empty bars (use Python SDK instead) ⚠️

**Fix:** Implement Go parquet decoding, or serve bars via Python sidecar, or use JSON fallback format alongside Parquet.

**Files:** `query-api/internal/reader/reader.go`

### 8. Collector — VCR Test Cassettes Directory

**Status:** Untracked. Add to `.gitignore` or commit intentionally.

**Files:** `collectors/tests/cassettes/`

---

## Phase 2 Priority

| Priority | Issue | Recommendation |
|----------|-------|----------------|
| P0 | 5: Go bars decoding | Add parquet reading to Go API, or route bars queries through Python SDK |
| P1 | 1: BigQuery | Use LOAD jobs instead of external tables |
| P2 | 8: Cassettes gitignore | Cleanup |
