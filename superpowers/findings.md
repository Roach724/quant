# Findings

## Architecture Discovery

### 1. All minute-level markets suffer intraday data loss

**Root cause:** Three layers combine destructively:
- `collectors/storage.py`: `blob.upload_from_string()` — **overwrites** GCS object each run
- `collectors/main.py`: `LOOKBACK_MINUTES=120` — each run fetches only last 2h
- `bigquery_loader/main.py`: `WRITE_TRUNCATE` — full partition replace

**Result:** Each collector run replaces the per-symbol-per-day parquet file with only the last lookback window of bars. Earlier intraday bars within the same day are lost forever. BQ loader freezes this partial state.

### 2. HK daily and minute data collide on same file path

Both HK collectors write to the identical GCS path:
`raw/hk/bars/year={Y}/month={M}/day={D}/symbol={S}.parquet`

Daily collector (09:00 UTC) overwrites minute collector's file from the same day. BQ loader at 09:30 UTC loads only 1 row of daily data — all minute bars lost.

### 3. `build_gcs_path()` has no frequency dimension

`data_type` is always hardcoded to `"bars"`. No `frequency` parameter anywhere in the path. Two different frequencies writing to the same symbol+date produce the same file name → overwrite.

### 4. No dedup logic exists anywhere

Searched entire repo for dedup/duplicate/upsert/merge — zero results.

### 5. US has no daily collector

Only 1m bars via `quant-collector-yfinance`. No US daily pipeline exists. Overwrite problem still applies to the 1m data.

### 6. Crypto is 1m only, 24/7

Single frequency, no collision. But overwrite problem is worse because 24/7 trading means more data lost per day.

### 7. Backfill uses same write function as normal collector

`collectors/backfill.py` calls the same `write_bars_to_gcs()`. No merge or dedup. Backfill and normal collector can overwrite each other.

## New Design: 6 Collectors × 6 Loaders × 6 Tables

All 3 markets (US, HK, crypto) collect **5m** and **1d** frequencies.

### GCS Path
```
raw/{us,hk,crypto}/bars/freq={5m,1d}/year=YYYY/month=MM/day=DD/symbol={SYMBOL}.parquet
```

### BQ Tables
`us_bars_5m`, `us_bars_1d`, `hk_bars_5m`, `hk_bars_1d`, `crypto_bars_5m`, `crypto_bars_1d`

### Write Mode
- `WRITE_APPEND` in BQ loader (was `WRITE_TRUNCATE`)
- Dedup via `ROW_NUMBER() OVER (PARTITION BY symbol, timestamp ORDER BY _ingest_time DESC)`
- `_ingest_time` column added by collector, used as dedup tiebreaker

## Code Paths Requiring Changes

### Python
| File | Change |
|------|--------|
| `collectors/storage.py` | Add `frequency` param to `build_gcs_path()`, add `_ingest_time` column |
| `collectors/main.py` | Pass `frequency` to `write_bars_to_gcs()` |
| `collectors/backfill.py` | Pass `frequency` to write and path functions |
| `bigquery_loader/main.py` | Add `_ingest_time` to schema, `FREQUENCY` env, `WRITE_APPEND`, dedup SQL |
| `sdk/quant/direct.py` | Add `frequency` param throughout 4 path-building functions |
| `sdk/quant/__init__.py` | Forward `frequency` to `bars_direct()` |
| `sdk/tests/test_direct.py` | Update test paths with `freq=5m/` |

### Go
| File | Change |
|------|--------|
| `query-api/internal/reader/reader.go` | Add freq to `buildGCSPrefix()`, `QueryBars()`, `ListSymbols()` |
| `query-api/internal/handler/handler.go` | Wire frequency to `ListSymbols()` |
| Test files | Update expected path strings |

### Terraform
| File | Change |
|------|--------|
| `terraform/bigquery.tf` | 6 tables + 6 loader jobs (was 3+3) |
| `terraform/cloud_run_jobs.tf` | 2 US collectors (was 1), merge scheduler |
| `terraform/cloud_run_hk.tf` | Rename HK jobs (5m/1d) |
| `terraform/cloud_run_crypto.tf` | 2 crypto collectors (was 1) |
| `terraform/scheduler.tf` | Delete (merged into job files) |
