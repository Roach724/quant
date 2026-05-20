# Task Plan: Append-Only Write + Frequency-Separated Storage

## Summary

Fix two critical data pipeline issues:
1. **Intraday data loss** — GCS overwrite + lookback window = only last ~2h preserved per day
2. **HK frequency collision** — daily and minute bars share the same GCS path/BQ table, overwriting each other

**Approach:** Plan B (BQ WRITE_APPEND + dedup on `_ingest_time`) + frequency-dimension in GCS paths + separate BQ tables per frequency.

**Strategy:** Destroy then rebuild. All existing Cloud Run jobs stopped/deleted, BQ tables dropped, GCS cleared. New resources provisioned from scratch.

**Frequency spec:** All 3 markets (US, HK, crypto) collect **5m** and **1d** bars.

## Phases

### Phase 0: Destroy Existing Resources
- [ ] **0.1** Stop and delete all 7 Cloud Run jobs
- [ ] **0.2** Delete all 7 Cloud Scheduler jobs
- [ ] **0.3** Drop all BQ tables: `us_bars`, `hk_bars`, `crypto_bars`
- [ ] **0.4** Clear GCS bucket (delete all objects under `raw/`)

### Phase 1: Core Storage Layer
- [ ] **1.1** Update `build_gcs_path()` in `collectors/storage.py` — add `frequency` parameter, new path format:
  `raw/{market}/bars/freq={freq}/year=.../month=.../day=.../symbol={S}.parquet`
- [ ] **1.2** Add `_ingest_time` column in `write_bars_to_gcs()` before writing Parquet
- [ ] **1.3** Update `write_bars_to_gcs()` signature — add `frequency` parameter
- [ ] **1.4** Update `collectors/main.py` — pass `frequency` to `write_bars_to_gcs()`
- [ ] **1.5** Update `collectors/backfill.py` — pass `frequency` to `write_bars_to_gcs()` and `build_gcs_path()`

### Phase 2: BigQuery Loader
- [ ] **2.1** Add `_ingest_time` to SCHEMA in `bigquery_loader/main.py`
- [ ] **2.2** Add `FREQUENCY` env var support
- [ ] **2.3** Update `load_day()` GCS glob to include `freq={FREQUENCY}/`
- [ ] **2.4** Change `write_disposition` from `WRITE_TRUNCATE` to `WRITE_APPEND`
- [ ] **2.5** Add dedup step after load: `CREATE OR REPLACE TABLE ... ROW_NUMBER() OVER (PARTITION BY symbol, timestamp ORDER BY _ingest_time DESC)`

### Phase 3: Python SDK
- [ ] **3.1** Add `frequency` parameter to `bars_direct()` in `sdk/quant/direct.py`
- [ ] **3.2** Update `_cache_path()` — insert `freq={frequency}/` into all 4 path-building functions
- [ ] **3.3** Update `sdk/quant/__init__.py` — forward `frequency` to `bars_direct()`
- [ ] **3.4** Update `sdk/tests/test_direct.py` — test paths include `freq=5m/`

### Phase 4: Go Query API
- [ ] **4.1** Update `buildGCSPrefix()` in `query-api/internal/reader/reader.go` — add frequency to path
- [ ] **4.2** Update `QueryBars()` — pass `params.Frequency` to `buildGCSPrefix()`
- [ ] **4.3** Update `ListSymbols()` — frequency-aware prefix scanning
- [ ] **4.4** Update handler and test files

### Phase 5: Terraform (rebuild from scratch)
- [ ] **5.1** `bigquery.tf` — create 6 frequency-specific BQ tables:
  `us_bars_5m`, `us_bars_1d`, `hk_bars_5m`, `hk_bars_1d`, `crypto_bars_5m`, `crypto_bars_1d`
  (all with `_ingest_time` TIMESTAMP column)
- [ ] **5.2** `bigquery.tf` — create 6 BQ loader jobs (one per market+freq)
- [ ] **5.3** `cloud_run_jobs.tf` — create 2 US collectors: `quant-collector-us-5m` + `quant-collector-us-1d` + schedulers
- [ ] **5.4** `cloud_run_hk.tf` — create 2 HK collectors: `quant-collector-hk-5m` + `quant-collector-hk-1d` + schedulers
- [ ] **5.5** `cloud_run_crypto.tf` — create 2 crypto collectors: `quant-collector-crypto-5m` + `quant-collector-crypto-1d` + schedulers
- [ ] **5.6** Delete `terraform/scheduler.tf` (merged into respective job files)
- [ ] **5.7** `terraform fmt && terraform validate && terraform plan`

### Phase 6: Deploy & Verify ⏳
- [ ] **6.1** `terraform apply` — provision all new resources
- [ ] **6.2** Python tests: `python -m pytest collectors/tests/ sdk/tests/ -v`
- [ ] **6.3** Go tests: `cd query-api && go vet ./... && go test ./... -v`
- [ ] **6.4** Trigger each collector → verify GCS path `raw/{market}/bars/freq={freq}/year=.../symbol=....parquet`
- [ ] **6.5** Trigger each BQ loader → verify no duplicate `(symbol, timestamp)` rows
- [ ] **6.6** SDK `source="direct"` and `source="api"` — both read from new paths correctly
- [ ] **6.7** Backfill: run for historical range → verify coexists with collector data, no duplicates

> ⏳ Phase 6 待 GCP 环境就绪后执行。

## Target Resource Map

### Collectors (6 Cloud Run Jobs)
| Job | Market | Freq | Lookback | Schedule |
|---|---|---|---|---|
| `quant-collector-us-5m` | us | 5m | 120 min | `*/5 * * * 1-5` ET |
| `quant-collector-us-1d` | us | 1d | 1440 min | `0 17 * * 1-5` ET |
| `quant-collector-hk-5m` | hk | 5m | 120 min | `*/5 1-8 * * 1-5` UTC |
| `quant-collector-hk-1d` | hk | 1d | 1440 min | `0 9 * * 1-5` UTC |
| `quant-collector-crypto-5m` | crypto | 5m | 120 min | `*/5 * * * *` UTC |
| `quant-collector-crypto-1d` | crypto | 1d | 1440 min | `0 1 * * *` UTC |

### BQ Loaders (6 Cloud Run Jobs)
| Job | Market | Freq | Table | Schedule |
|---|---|---|---|---|
| `quant-bq-loader-us-5m` | us | 5m | `us_bars_5m` | `0 6 * * 1-5` ET |
| `quant-bq-loader-us-1d` | us | 1d | `us_bars_1d` | `0 6 * * 1-5` ET |
| `quant-bq-loader-hk-5m` | hk | 5m | `hk_bars_5m` | `30 9 * * 1-5` UTC |
| `quant-bq-loader-hk-1d` | hk | 1d | `hk_bars_1d` | `30 9 * * 1-5` UTC |
| `quant-bq-loader-crypto-5m` | crypto | 5m | `crypto_bars_5m` | `0 6 * * *` UTC |
| `quant-bq-loader-crypto-1d` | crypto | 1d | `crypto_bars_1d` | `6 0 * * *` UTC |

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
