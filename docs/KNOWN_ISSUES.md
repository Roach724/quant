# Known Issues

Phase 1 deployment issues to resolve before or during Phase 2.

## 1. BigQuery External Table — Hive Partition Format Mismatch

**Status:** Deferred (`terraform/bigquery.tf.defer`)

The storage layout uses bare directory names (`2026/05/15/AAPL.parquet`), but BigQuery external tables with `CUSTOM` hive partitioning require `key=value` format (`year=2026/month=05/day=15/symbol=AAPL.parquet`). `AUTO` mode with wildcard patterns also fails because files are in subdirectories.

**Fix options:**
- Change `collectors/storage.py:build_gcs_path()` to emit Hive-style paths
- Migrate existing GCS data to new layout
- OR use BigQuery load jobs instead of external tables (copy Parquet into native tables)

**Files:** `terraform/bigquery.tf.defer`, `collectors/storage.py`

## 2. Query API Public Access Blocked by Org Policy

**Status:** Workaround in place (authenticated requests)

`allUsers` IAM binding on Cloud Run is rejected by the GCP organization policy. The API requires a Bearer token from `gcloud auth print-identity-token` for access.

**Impact:**
- Python SDK client (`sdk/quant/client.py`) cannot call the API without auth
- Jupyter notebooks need credential injection to use `source="api"` mode
- `source="direct"` mode works with local Parquet files but not yet wired to GCS

**Fix options:**
- Request org policy exemption for `roles/run.invoker` → `allUsers`
- OR implement IAP (Identity-Aware Proxy) in front of Cloud Run
- OR add service account key auth path to Python SDK client
- OR route all SDK usage through `source="direct"` with GCS credentials

**Files:** `terraform/cloud_run_api.tf` (commented IAM), `sdk/quant/client.py`

## 3. Python SDK — Direct GCS Mode Not Connected

**Status:** Not implemented

`sdk/quant/direct.py` only reads from local filesystem (`base_path` parameter). For production use from notebooks, it needs to read from `gs://` URIs.

**Fix:** Add `gcsfs` dependency to `sdk/pyproject.toml`, update `bars_direct()` to resolve `gs://` paths via `gcsfs.GCSFileSystem`.

**Files:** `sdk/quant/direct.py`, `sdk/pyproject.toml`

## 4. Go Query API — Symbol Listing Returns Empty

**Status:** Known limitation (Phase 1)

`GET /api/v1/symbols` always returns `[]`. The handler has a placeholder comment at `handler.go:line 29`. Symbol index should be built by scanning GCS listing or reading `metadata/{market}/symbols.json`.

**Fix:** Implement GCS prefix listing in the handler, cache results, populate from collector metadata.

**Files:** `query-api/internal/handler/handler.go`

## 5. Go Query API — Bars Endpoint Returns Empty

**Status:** Known limitation (Phase 1)

`GET /api/v1/bars` always returns `{"bars": [], "status": "ok"}`. The real GCS Parquet reader path (streaming + filtering from GCS) is not yet implemented. The handler has a stub at `handler.go:line 50`.

**Fix:** Implement `query-api/internal/reader/reader.go` GCS integration — list blobs matching prefix, stream-read Parquet, filter by time range, return JSON.

**Files:** `query-api/internal/handler/handler.go`, `query-api/internal/reader/reader.go`

## 6. Cloud Run Memory Floor (512 MiB)

**Status:** Applied (fixed)

Cloud Run always-allocated CPU requires ≥ 512 MiB memory. Original terraform specified 256 MiB for query-api. Increased to 512 MiB during deployment.

**Files:** `terraform/cloud_run_api.tf`

## 7. Terraform 1.9 Inline Block Syntax

**Status:** Applied (fixed)

Terraform ≥1.9 rejects single-line blocks with multiple arguments. Fixed `action { type = ..., storage_class = ... }` → multi-line in `storage.tf`.

**Files:** `terraform/storage.tf`

## 8. Collector — VCR Test Cassettes Directory

**Status:** Untracked, not committed

`collectors/tests/cassettes/` contains recorded API responses from VCR.py test runs. Not committed to version control. Should be `.gitignore`d or added if intentional.

**Files:** `collectors/tests/cassettes/`

## Priority for Phase 2

| Priority | Issue | Blocks |
|----------|-------|--------|
| P0 | Issue 3: SDK direct GCS mode | Research notebooks can't read production data |
| P0 | Issue 5: Go bars endpoint | Query API has no real data path |
| P1 | Issue 2: API auth | SDK client can't call API without manual token |
| P1 | Issue 1: BigQuery | No SQL analytics on collected data |
| P2 | Issue 4: Symbol listing | Nice-to-have, not blocking |
| P2 | Issue 8: Cassettes gitignore | Cleanup |
