# Phase 5: Crypto Market Integration — Design Document

**Date:** 2026-05-16
**Author:** Jarvis (Coordinator) + Derick (Reviewer)
**Branch:** `phase5-crypto`
**Status:** Approved

---

## 1. Purpose

Extend the quant trading system to support cryptocurrency markets via Binance, reusing the existing framework with minimal architectural change. Target: beginner-friendly crypto quant trading — free data, no KYC for data, low capital requirements.

---

## 2. Architecture Overview

```
                         Existing Framework                   Crypto Addition
                         ─────────────────                    ──────────────

Data Layer    MarketAdapter Protocol → GCS Parquet   ← CryptoBinanceAdapter (ccxt)
                                                   ← market="crypto" path

Research      DataFrameSource ← SDK direct          ← ZERO changes
              Engine.run() ← Strategy               ← ZERO changes

Execution     Broker Protocol → PaperBroker          ← CryptoPaperBroker
              Broker Protocol → AlpacaBroker         ← CryptoBinanceBroker
              OrderManager / RiskGateway             ← ZERO changes (after qty upgrade)

Infra         Terraform → Cloud Run Job              ← quant-collector-crypto-binance
              Terraform → Cloud Scheduler            ← */5 * * * * (24×7)
              Terraform → BigQuery ext table         ← crypto_bars

Go API        market.go constants                    ← add CRYPTO constant
```

**Key insight:** The framework was designed with market-agnostic protocols. ~80% of this phase is adding adapters; ~20% is configuration.

---

## 3. Component Design

### 3.1 Data Layer — CryptoBinanceAdapter

**File:** `collectors/adapters/crypto_binance_adapter.py`

| Aspect | Design |
|--------|--------|
| Library | `ccxt` (unified crypto exchange API) |
| Exchange | Binance (largest, best liquidity, free historical data) |
| Market | `"CRYPTO"` |
| Symbols | Top 20 USDT perpetual pairs |
| Frequency | 1m, 5m, 15m, 1h, 1d (full Binance range) |
| Auth | None required for public OHLCV data |
| Rate limit | ccxt handles `enableRateLimit: true` automatically |
| Market hours | 24×7: `(00:00, 23:59)` |
| Symbol format | ccxt: `BTC/USDT` → stored: `BTCUSDT` |

**Differences from yfinance adapter:**

| Property | YFinanceUSAdapter | CryptoBinanceAdapter |
|----------|-------------------|---------------------|
| volume type | int | float → cast to int for schema |
| symbol format | AAPL | BTCUSDT (strip `/`) |
| trading hours | 09:30-16:00 EST | 00:00-23:59 UTC |
| API key | optional (yfinance) | not needed (public data) |
| pagination | built-in | manual via ccxt `since` + `limit=1000` |

**ccxt fetch_bars pagination:**
```python
def fetch_bars(self, symbols, start, end, frequency):
    since_ms = int(start.timestamp() * 1000)
    limit = 1000  # Binance max per request
    all_ohlcv = []
    for symbol in symbols:
        current_since = since_ms
        while current_since < int(end.timestamp() * 1000):
            ohlcv = self._exchange.fetch_ohlcv(symbol, tf, current_since, limit)
            if not ohlcv:
                break
            all_ohlcv.extend([(symbol, *row) for row in ohlcv])
            current_since = ohlcv[-1][0] + 1
    # Normalize to Bar schema DataFrame
```

**Storage path:** `gs://<bucket>/raw/crypto/bars/year=YYYY/month=MM/day=DD/symbol=BTCUSDT.parquet`

Existing `storage.py` and `build_gcs_path()` are fully compatible — just pass `market="crypto"`.

---

### 3.2 Collector Entrypoint

**File:** `collectors/main.py`

One-line addition to `get_adapter()`:
```python
def get_adapter(source: str):
    if source == "alpaca":
        return AlpacaUSAdapter(...)
    if source == "cryptobinance":        # 🆕
        return CryptoBinanceAdapter()     # 🆕
    return YFinanceUSAdapter()
```

Same Docker image, different env vars. Zero Dockerfile changes.

---

### 3.3 Execution Layer — Broker Protocol Upgrade

**Decision A:** `qty: int` → `qty: float`

**Affected files:**
1. `oms/broker/__init__.py` — `BrokerOrder.qty`, `BrokerPosition.qty`, all method signatures
2. `oms/broker/alpaca_broker.py` — Alpaca expects int, wrap with `int()` internally
3. `oms/state.py` — `TrackedOrder.qty`
4. `oms/position.py` — `PositionTracker` quantity tracking
5. `oms/manager.py` — `OrderManager.submit()` parameter
6. `oms/bridge.py` — `convert_signal()` qty computation
7. All test files that create orders/positions

**Rationale:** float qty is a superset of int. USD stocks can still use integers. Crypto (0.001 BTC), forex (1000 units), and futures (contracts) all need floats. This is the right time to upgrade.

---

### 3.4 CryptoPaperBroker

**File:** `oms/broker/crypto_broker.py`

Implements `Broker` protocol. Logically identical to `PaperBroker` but:
- Uses `ccxt.binance()` for price data (fetch_ticker)
- No API key needed for paper mode
- Supports fractional quantities (float qty)

```python
class CryptoPaperBroker:
    def __init__(self, initial_capital: float = 10_000.0, quote_currency: str = "USDT"):
        self.cash = initial_capital
        self._positions: dict[str, BrokerPosition] = {}
        self._orders: dict[str, BrokerOrder] = {}
        self._exchange = ccxt.binance({"enableRateLimit": True})
        # Same logic as PaperBroker, adapted for crypto

    async def submit_order(self, symbol, side, qty, order_type="market", limit_price=None):
        # Identical flow to PaperBroker
        # Market orders: fill at current price ± small random slip
        # Limit orders: pending until price crosses
```

---

### 3.5 CryptoBinanceBroker

**File:** `oms/broker/crypto_broker.py` (same file, separate class)

```python
class CryptoBinanceBroker:
    def __init__(self, api_key, api_secret, testnet=True):
        config = {"apiKey": api_key, "secret": api_secret, "enableRateLimit": True}
        if testnet:
            config["urls"] = {"api": "https://testnet.binancefuture.com"}
        self._exchange = ccxt.binance(config)

    async def submit_order(self, symbol, side, qty, order_type="market", limit_price=None):
        # ccxt.create_order() → normalize to BrokerOrder
    async def cancel_order(self, broker_id):
        # ccxt.cancel_order()
    async def get_positions(self):
        # ccxt.fetch_positions() for futures, fetch_balance() for spot
    async def get_account(self):
        # ccxt.fetch_balance()
    async def get_open_orders(self):
        # ccxt.fetch_open_orders()
```

**Testnet first:** Binance Futures Testnet is free and mirrors production. All paper→live pipeline validated before real money.

---

### 3.6 Go Query API

**File:** `query-api/internal/market/market.go`

Add CRYPTO to the market constants and `ParseMarket()` switch:
```go
const (
    US     Market = "US"
    CN     Market = "CN"
    HK     Market = "HK"
    CRYPTO Market = "CRYPTO"  // 🆕
)

func ParseMarket(s string) (Market, bool) {
    switch strings.ToUpper(s) {
    case "US": return US, true
    case "CN": return CN, true
    case "HK": return HK, true
    case "CRYPTO": return CRYPTO, true  // 🆕
    default: return "", false
    }
}
```

No other changes needed — `StoragePrefix()`, `Bar`, and reader/handler are market-parameterized.

---

### 3.7 Infrastructure (Terraform)

**New file:** `terraform/cloud_run_crypto.tf`

```hcl
# Cloud Run Job for crypto data collection
resource "google_cloud_run_v2_job" "collector_crypto" {
  name     = "quant-collector-crypto-binance"
  location = var.region
  # ... reuses same service account, Docker image, resource limits
  env {
    name  = "COLLECTOR_SOURCE"; value = "cryptobinance"
  }
  env {
    name  = "SYMBOLS"
    value = "BTC/USDT,ETH/USDT,SOL/USDT,BNB/USDT,XRP/USDT,DOGE/USDT,ADA/USDT,AVAX/USDT,DOT/USDT,LINK/USDT"
  }
}

# 24×7 scheduler (crypto never closes)
resource "google_cloud_scheduler_job" "collect_crypto_bars" {
  name     = "quant-collect-crypto-bars"
  schedule = "*/5 * * * *"  # every 5 minutes, all days
  # ...
}

# BigQuery external table
resource "google_bigquery_table" "crypto_bars" {
  external_data_configuration {
    source_uris = ["gs://${bucket}/raw/crypto/bars/*/*/*/*.parquet"]
    hive_partitioning_options { mode = "AUTO" }
  }
}
```

**No changes to existing terraform files** — this is an additive file only.

---

## 4. Data Flow (End-to-End)

```
1. Cloud Scheduler triggers every 5 min
2. Cloud Run Job starts collector Docker container
3. get_adapter("cryptobinance") → CryptoBinanceAdapter
4. fetch_bars(symbols, start, end) → pd.DataFrame (Bar schema)
5. write_bars_to_gcs(df, bucket, market="crypto")
   → gs://bucket/raw/crypto/bars/year=2026/month=05/day=16/symbol=BTCUSDT.parquet
   → gs://bucket/raw/crypto/bars/year=2026/month=05/day=16/symbol=BTCUSDT.json
6. BQ loader picks up parquet → BigQuery crypto_bars table
7. Go Query API serves /api/v1/bars?market=crypto&symbols=BTCUSDT&...
8. SDK: quant.bars(["BTCUSDT"], "2026-01-01", "2026-05-16", market="crypto", source="direct")
9. Engine: DataFrameSource(close=df.pivot(...)) → Engine.run(strategy, data)
10. Bridge: forward_signal → CryptoPaperBroker (backtest) or CryptoBinanceBroker (live)
```

---

## 5. Testing Strategy

| Layer | Test File | Type | Count |
|-------|-----------|------|-------|
| Adapter | `collectors/tests/test_crypto_adapter.py` | Unit + VCR | ~5 |
| CryptoPaperBroker | `oms/tests/test_crypto_broker.py` | Unit | ~5 |
| CryptoBinanceBroker | `oms/tests/test_crypto_broker.py` | VCR (testnet) | ~3 |
| Integration | `oms/tests/test_integration.py` | Add crypto case | ~1 |
| Broker qty upgrade | `oms/tests/test_*.py` | Fix all existing | ~30 (update) |
| Go API | `query-api/internal/market/market_test.go` | Unit | ~2 |
| Terraform | `terraform validate` | Static | 1 |

**Total: ~17 new tests + ~30 existing test updates**

---

## 6. Scope Boundaries (YAGNI)

**In scope:**
- Binance USDT perpetual futures data pipeline
- CryptoPaperBroker for strategy backtesting
- CryptoBinanceBroker for live/paper trading via ccxt
- qty int→float protocol upgrade
- Go API CRYPTO market constant
- Terraform infrastructure

**Out of scope (future phases):**
- Spot trading (futures first)
- Multiple exchanges (OKX, Bybit — ccxt makes this trivial later)
- WebSocket streaming (Phase 6)
- Portfolio margin calculations
- Cross-exchange arbitrage
- On-chain data / DeFi

---

## 7. Dependencies

```
pip install ccxt         # Data + Broker
pip install pytest-vcr   # Already in dev deps
# No new GCP dependencies — reuses existing project
```

---

## 8. Design Decisions Log

| # | Decision | Rationale |
|---|----------|-----------|
| 1 | Binance over others | Largest volume, best API docs, ccxt support is best-tested |
| 2 | ccxt over binance-connector | Single library for all exchanges, future-proof |
| 3 | Futures over spot | No borrowing/shorting restrictions, unified margin |
| 4 | qty float upgrade | Superset of int, needed for crypto/fx/futures |
| 5 | Reuse collector Docker image | DRY, zero deployment surface increase |
| 6 | 24×7 scheduler | Crypto never closes, no market-hours filtering needed |
| 7 | Testnet first | Free, mirrors production, safe development |
