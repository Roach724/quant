# Task Plan: Append-Only Write + Frequency-Separated Storage

> Updated: 2026-05-26 | Branch: `feature/futu-integration`

## Summary

Fix two critical data pipeline issues:
1. **Intraday data loss** — GCS overwrite + lookback window = only last ~2h preserved per day
2. **HK frequency collision** — daily and minute bars share the same GCS path/BQ table, overwriting each other

**Approach:** Plan B (BQ WRITE_APPEND + dedup on `_ingest_time`) + frequency-dimension in GCS paths + separate BQ tables per frequency.

**Strategy:** Destroy then rebuild. All existing Cloud Run jobs stopped/deleted, BQ tables dropped, GCS cleared. New resources provisioned from scratch.

**Frequency spec:** All 3 markets (US, HK, crypto) collect **5m** and **1d** bars.

## Status Overview

| Phase | Scope | Status |
|-------|-------|--------|
| Phase 0 | Destroy old GCP resources | ⏳ Pending (GCP access required) |
| Phase 1 | Core Storage Layer (`collectors/storage.py`) | ✅ Code complete + tests pass |
| Phase 2 | BigQuery Loader (`bigquery_loader/main.py`) | ✅ Code complete |
| Phase 3 | Python SDK (`sdk/quant/direct.py`, `__init__.py`) | ✅ Code complete + 3/3 tests pass |
| Phase 4 | Go Query API (`query-api/internal/reader/reader.go`) | ✅ Code complete (Go not local, builds in Docker) |
| Phase 5 | Terraform (6 tables + 6 collectors + 6 BQ loaders) | ✅ IaC complete + `terraform validate` passes |
| Phase 6 | Deploy & Verify (GCP operations) | ⏳ Pending (GCP access required) |

**Code verification (2026-05-26): 254 tests passed across engine/oms/collectors/sdk/factors/ml/experiment/paper.**

---

## Phases

### Phase 0: Destroy Existing Resources ⏳

> Requires: GCP project access (`deductive-notch-495015-c2`, region `asia-east2`)

- [ ] **0.1** Stop and delete all existing Cloud Run jobs (old format: no freq dimension)
- [ ] **0.2** Delete all existing Cloud Scheduler jobs
- [ ] **0.3** Drop old BQ tables: `us_bars`, `hk_bars`, `crypto_bars` (single-table format)
- [ ] **0.4** Clear GCS bucket (delete all objects under `raw/`)

### Phase 1: Core Storage Layer ✅

- [x] **1.1** Update `build_gcs_path()` in `collectors/storage.py` — `frequency` parameter, new path format:
  `raw/{market}/bars/freq={freq}/year=.../month=.../day=.../symbol={S}.parquet`
- [x] **1.2** Add `_ingest_time` column in `write_bars_to_gcs()` before writing Parquet
- [x] **1.3** Update `write_bars_to_gcs()` signature — `frequency` parameter already present
- [x] **1.4** Update `collectors/main.py` — passes `frequency` to `write_bars_to_gcs()`
- [x] **1.5** Update `collectors/backfill.py` — passes `frequency` to `write_bars_to_gcs()` and `_write_local()`

### Phase 2: BigQuery Loader ✅

- [x] **2.1** Add `_ingest_time` to SCHEMA in `bigquery_loader/main.py`
- [x] **2.2** Add `FREQUENCY` env var support
- [x] **2.3** Update `load_day()` GCS glob to include `freq={FREQUENCY}/`
- [x] **2.4** Change `write_disposition` from `WRITE_TRUNCATE` to `WRITE_APPEND`
- [x] **2.5** Add dedup step after load: `CREATE OR REPLACE TABLE ... ROW_NUMBER() OVER (PARTITION BY symbol, timestamp ORDER BY _ingest_time DESC)`

### Phase 3: Python SDK ✅

- [x] **3.1** Add `frequency` parameter to `bars_direct()` in `sdk/quant/direct.py`
- [x] **3.2** Update `_cache_path()` — `freq={frequency}/` in all 4 path-building functions
- [x] **3.3** Update `sdk/quant/__init__.py` — forwards `frequency` to `bars_direct()`
- [x] **3.4** `sdk/tests/test_direct.py` — 3/3 tests pass with new paths

### Phase 4: Go Query API ✅

- [x] **4.1** Update `buildGCSPrefix()` in `query-api/internal/reader/reader.go` — frequency param in path
- [x] **4.2** Update `QueryBars()` — passes `params.Frequency` to `buildGCSPrefix()`
- [x] **4.3** Update `ListSymbols()` — frequency-aware prefix scanning
- [x] **4.4** Update handler — `handler.go` passes frequency through from query params

### Phase 5: Terraform (rebuild from scratch) ✅

- [x] **5.1** `bigquery.tf` — 6 frequency-specific BQ tables:
  `us_bars_5m`, `us_bars_1d`, `hk_bars_5m`, `hk_bars_1d`, `crypto_bars_5m`, `crypto_bars_1d`
  (all with `_ingest_time` TIMESTAMP column, partitioned by `timestamp` DAY, clustered by `symbol`)
- [x] **5.2** `bigquery.tf` — 6 BQ loader jobs via `for_each` (one per market+freq)
- [x] **5.3** `cloud_run_jobs.tf` — 2 US collectors: `quant-collector-us-5m` + `quant-collector-us-1d` + schedulers
- [x] **5.4** `cloud_run_hk.tf` — 2 HK collectors: `quant-collector-hk-5m` + `quant-collector-hk-1d` + schedulers
- [x] **5.5** `cloud_run_crypto.tf` — 2 crypto collectors: `quant-collector-crypto-5m` + `quant-collector-crypto-1d` + schedulers
- [x] **5.6** `terraform/scheduler.tf` — deleted (merged into respective job files, confirmed not present)
- [x] **5.7** `terraform fmt && terraform validate && terraform plan` — `validate` passes

### Phase 6: Deploy & Verify (2026-05-27)

> Status: 14/14 Cloud Run Jobs deployed. Futu collectors paused awaiting OpenD network access.

- [x] **6.1** `terraform apply` — All 14 jobs + 12 schedulers + 6 BQ tables + query-api provisioned
- [x] **6.2** Python tests: 320 tests pass (including 66 Futu tests with OpenD running)
- [ ] **6.3** Go tests: Docker image built & pushed (query-api:latest), tests run in container
- [x] **6.4** GCS paths verified: `raw/{market}/bars/freq={freq}/` format confirmed, data flowing
- [x] **6.5** BQ tables: 6 freq-separated, re-accumulating after full reset (76.3k objects cleared)
- [ ] **6.6** SDK from production: local gcsfs DNS issue; Go API (`source="api"`) works
- [ ] **6.7** Backfill: not yet run for historical range
- [x] **6.8** Collector image rebuilt with futu-api, pushed; Futu collectors use same image
- [x] **6.9** BQ loader + collector + query-api images: all built & pushed

**Futu blockers:** `OPEND_HOST=127.0.0.1` not reachable from Cloud Run. Options:
A. Embed OpenD in container (Dockerfile.collector exists, needs linux/amd64 build)
B. Expose user's OpenD to internet (ngrok/Cloudflare Tunnel)
C. Run Futu collectors locally instead of Cloud Run

---

## Data Reset (2026-05-27)

All 6 BQ tables truncated + 76.3k GCS objects deleted. Fresh data accumulation started:
- US 5m: 194 rows (yfinance, during market hours) ✅
- Crypto 5m: 240 rows (Binance, 24/7) ✅
- HK: 0 (market closed at midnight) ⏳

## Beyond Phase 0: Futu Collector Deployment

Once Phase 0 is deployed with freq-separated paths, Futu collectors can be deployed:

- [ ] **F1** Verify OpenD environment (local/VPS with OpenD binary + login)
- [ ] **F2** `terraform apply` for `collector-futu-stock.tf` + `collector-futu-crypto.tf`
- [ ] **F3** Write Futu data to `freq=futu_1m` / `freq=futu_1d` paths (parallel to yfinance/Binance)
- [ ] **F4** End-to-end verification: OpenD → FutuStockAdapter → GCS → BQ → SDK → Engine backtest

---

## Target Resource Map

### Collectors (6 + 2 Futu Cloud Run Jobs)
| Job | Market | Freq | Lookback | Schedule |
|---|---|---|---|---|
| `quant-collector-us-5m` | us | 5m | 120 min | `*/5 * * * 1-5` ET |
| `quant-collector-us-1d` | us | 1d | 1440 min | `0 17 * * 1-5` ET |
| `quant-collector-hk-5m` | hk | 5m | 120 min | `*/5 1-8 * * 1-5` UTC |
| `quant-collector-hk-1d` | hk | 1d | 1440 min | `0 9 * * 1-5` UTC |
| `quant-collector-crypto-5m` | crypto | 5m | 120 min | `*/5 * * * *` UTC |
| `quant-collector-crypto-1d` | crypto | 1d | 1440 min | `0 1 * * *` UTC |
| `quant-collector-futu-stock` | hk+us | futu_1m | — | `*/30 1-10 * * 1-5` HKT |
| `quant-collector-futu-crypto` | crypto | futu_1m | — | `*/5 * * * *` UTC |

### BQ Loaders (6 Cloud Run Jobs)
| Job | Market | Freq | Table | Schedule |
|---|---|---|---|---|
| `quant-bq-loader-us-5m` | us | 5m | `us_bars_5m` | `0 6 * * 1-5` ET |
| `quant-bq-loader-us-1d` | us | 1d | `us_bars_1d` | `0 6 * * 1-5` ET |
| `quant-bq-loader-hk-5m` | hk | 5m | `hk_bars_5m` | `30 9 * * 1-5` UTC |
| `quant-bq-loader-hk-1d` | hk | 1d | `hk_bars_1d` | `30 9 * * 1-5` UTC |
| `quant-bq-loader-crypto-5m` | crypto | 5m | `crypto_bars_5m` | `0 6 * * *` UTC |
| `quant-bq-loader-crypto-1d` | crypto | 1d | `crypto_bars_1d` | `0 1 * * *` UTC |

### BQ Tables (6, all in `quant` dataset, partitioned by `timestamp` DAY, clustered by `symbol`)
| Table | Market | Freq |
|---|---|---|
| `us_bars_5m` | us | 5m |
| `us_bars_1d` | us | 1d |
| `hk_bars_5m` | hk | 5m |
| `hk_bars_1d` | hk | 1d |
| `crypto_bars_5m` | crypto | 5m |
| `crypto_bars_1d` | crypto | 1d |

### GCS Path Convention
```
raw/{us,hk,crypto}/bars/freq={5m,1d}/year=YYYY/month=MM/day=DD/symbol={SYMBOL}.parquet
```

## Decisions

| Decision | Rationale |
|----------|-----------|
| Destroy-then-rebuild | No migration complexity, consistent naming, clean GCS state |
| Plan B (BQ dedup on `_ingest_time`) | Single change point, no GCS file explosion |
| `freq={freq}` in Hive path | Consistent with existing partitioning, BQ-compatible glob |
| One BQ loader per (market, freq) | Clean 1:1 mapping, FREQUENCY env var maps directly |
| Full-table dedup after WRITE_APPEND | Simple, correct; can optimize to partition-filtered later |
| Merge scheduler.tf into job files | Each collector/BQ-loader co-located with its trigger |
| All 3 markets = 5m + 1d | Unified frequency spec, simpler mental model |
| US 1d schedule: 17:00 ET (after close) | Market closes 16:00 ET, data available shortly after |
| Crypto 1d schedule: 00:01 UTC | Binance daily candle closes at 00:00 UTC |
| 5m lookback: 120 min (24 bars) | With append mode, only needs to cover missed runs |
| 1d lookback: 1440 min (1 day) | Sufficient for daily bars, dedup handles overlap |
| Futu as separate freq paths (`futu_1m`, `futu_1d`) | Parallel to yfinance/Binance, no collision, easy switch |
