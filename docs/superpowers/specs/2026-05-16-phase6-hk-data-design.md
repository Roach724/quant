# Phase 6: Hong Kong Market Data Integration — Design Document

**Date:** 2026-05-16
**Branch:** `phase6-hk-data`
**Status:** Approved

---

## 1. Purpose

Add Hong Kong stock market (HKEX) data pipeline to the quant system. Use the existing yfinance adapter pattern — minimal code, maximum reuse. Support both daily (full history) and minute-level (rolling 7-day window, same as US) collection.

---

## 2. Architecture

```
YFinanceHKAdapter (new)  →  Collector main.py (+yfinancehk source)
                        →  GCS: raw/hk/bars/... (reuses storage.py)
                        →  Go API: market=hk (ALREADY SUPPORTED since Phase 1)
                        →  SDK: market="hk" (ALREADY SUPPORTED)
                        →  BQ Loader: MARKET=hk TABLE=hk_bars (ALREADY PARAMETERIZED)
                        →  Engine: DataFrameSource (ZERO CHANGES)

NOT in scope: HK broker/execution layer (use engine only for now)
```

### Why this is much simpler than Crypto (Phase 5)

| What Crypto needed | What HK needs |
|--------------------|---------------|
| New market constant CRYPTO | ✅ HK already exists |
| qty int→float upgrade | ✅ Already done |
| BQ Loader parameterization | ✅ Already done |
| CryptoPaperBroker + CryptoBinanceBroker | ✅ Not needed yet (data only) |
| ccxt dependency | ✅ yfinance already installed |

**Net new code: ~150 lines (adapter + tests + terraform)**

---

## 3. YFinanceHKAdapter

### 3.1 Design

| Aspect | Value |
|--------|-------|
| File | `collectors/adapters/yfinance_hk_adapter.py` |
| Library | `yfinance` (already a dependency) |
| Market | `"HK"` |
| Symbol format | yfinance: `0700.HK`, `9988.HK`, `0005.HK` |
| Frequency | 1d (full history), 1h (~730d), 1m/5m (~7d) |
| Market hours | 09:30–16:00 HKT (UTC+8) |
| Auth | None (free, no API key) |

### 3.2 Symbols — HS Tech + Blue Chips (~30 stocks)

```python
_HK_SYMBOLS = [
    # Tech / Internet
    "0700.HK",   # Tencent
    "9988.HK",   # Alibaba
    "3690.HK",   # Meituan
    "9618.HK",   # JD.com
    "9999.HK",   # NetEase
    "9888.HK",   # Baidu
    "2015.HK",   # Li Auto
    "9868.HK",   # XPeng
    "1810.HK",   # Xiaomi
    "1024.HK",   # Kuaishou
    "9626.HK",   # Bilibili
    # Finance / Property
    "0005.HK",   # HSBC
    "0388.HK",   # HKEX
    "1299.HK",   # AIA
    "2318.HK",   # Ping An
    "3968.HK",   # CM Bank
    "1398.HK",   # ICBC
    "3988.HK",   # Bank of China
    "2628.HK",   # China Life
    "0011.HK",   # Hang Seng Bank
    "# Conglomerates / Energy / Consumer
    "0001.HK",   # CK Hutchison
    "0002.HK",   # CLP Holdings
    "0003.HK",   # HK & China Gas
    "0016.HK",   # SHK Properties
    "0027.HK",   # Galaxy Ent
    "0175.HK",   # Geely
    "0267.HK",   # CITIC
    "0291.HK",   # CR Beer
    "0669.HK",   # Techtronic
    "0823.HK",   # Link REIT
    "0883.HK",   # CNOOC
    "0941.HK",   # China Mobile
    "1044.HK",   # Hengan Intl
    "1093.HK",   # CSPC Pharma
    "1177.HK",   # Sino Biopharm
    "1928.HK",   # Sands China
    "2269.HK",   # WuXi Biologics
]
```

### 3.3 Implementation pattern

Identical to `YFinanceUSAdapter.fetch_bars()` — use `yf.Tickers()` and `.history()`, normalize to Bar schema. Key differences:
- `market = "HK"` (not "US")
- Symbol suffix `.HK` stripped for storage (e.g., `0700` not `0700.HK`)
- Market hours: 09:30-16:00 HKT

---

## 4. Infrastructure

### 4.1 Collector — two modes

| Job | Frequency | Env | Schedule |
|-----|-----------|-----|----------|
| `quant-collector-hk-daily` | 1d | FREQUENCY=1d, LOOKBACK_MINUTES=1440 | Daily 17:00 HKT |
| `quant-collector-hk-minute` | 1m | FREQUENCY=1m, LOOKBACK_MINUTES=120 | */5 Mon-Fri HKT |

Both reuse the same collector Docker image with different env vars.

### 4.2 BigQuery

Native table `hk_bars` — same schema as `us_bars` and `crypto_bars`. Loaded by `quant-bq-loader-hk` job.

### 4.3 Terraform

New file: `terraform/cloud_run_hk.tf` — follows exact pattern of `cloud_run_crypto.tf`.

---

## 5. NOT in Scope

- Futu OpenAPI adapter (Phase 6b, minutes quality upgrade)
- HK broker/execution (needs IB/HK brokerage first)
- HK-specific risk rules (T+2 settlement, short-sale restrictions)
- HK Stock Connect northbound data
- HK corporate actions (dividends, stock splits)

---

## 6. Data Flow (end-to-end)

```
Cloud Scheduler → Cloud Run Job (collector image, SOURCE=yfinancehk)
  → YFinanceHKAdapter.fetch_bars(["0700.HK", ...], start, end, "1m")
  → pd.DataFrame (Bar schema, market="HK")
  → write_bars_to_gcs(df, bucket, market="hk")
  → gs://bucket/raw/hk/bars/year=2026/month=05/day=16/symbol=0700.parquet
  → BQ Loader (MARKET=hk, TABLE=hk_bars) → BigQuery hk_bars table
  → Go API: /api/v1/bars?market=hk&symbols=0700,9988&...
  → SDK: quant.bars(["0700","9988"], "2026-01-01", "2026-05-16", market="hk")
  → Engine: DataFrameSource → Strategy → Backtest
```
