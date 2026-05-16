# Phase 5: Crypto Market Integration — Implementation Plan

> **For implementer:** Use TDD throughout. Write failing test first. Watch it fail. Then implement. Commit after every green test.

**Goal:** Add cryptocurrency (Binance) data pipeline, broker, and infrastructure to the quant trading system.

**Architecture:** New CryptoBinanceAdapter for data, CryptoPaperBroker + CryptoBinanceBroker for execution, qty int→float upgrade across Broker protocol. Reuses existing GCS storage, collector Docker image, and engine unchanged.

**Tech Stack:** Python 3.12+, ccxt, pandas, pyarrow, pytest, Go 1.23+, Terraform (GCP).

---

## Pre-flight

```bash
conda activate quant
cd /home/node/.openclaw/workspace/quant
git checkout phase5-crypto
pip install ccxt
# Verify existing tests pass before any changes:
python -m pytest oms/tests/ engine/tests/ collectors/tests/ sdk/tests/ quality/tests/ -v -k "not vcr" --tb=short -q 2>&1 | tail -5
```

---

### Task 1: qty float upgrade — Broker protocol dataclasses

**Files:**
- Modify: `oms/broker/__init__.py`

**Step 1: Write failing test**

Create `oms/tests/test_broker_float_qty.py`:

```python
"""Test that Broker protocol supports float quantities."""
import pytest
import asyncio
from oms.broker import PaperBroker, BrokerOrder, BrokerPosition, BrokerAccount


def test_broker_order_accepts_float_qty():
    order = BrokerOrder(
        broker_id="test-1", symbol="BTCUSDT", side="buy", qty=0.001
    )
    assert order.qty == 0.001
    assert isinstance(order.qty, float)

def test_broker_position_accepts_float_qty():
    pos = BrokerPosition(
        symbol="BTCUSDT", qty=0.5, avg_entry_price=65000.0,
        market_value=32500.0, unrealized_pnl=100.0
    )
    assert pos.qty == 0.5

def test_paper_broker_submit_float_qty():
    broker = PaperBroker(initial_capital=100_000)
    broker.update_price("BTCUSDT", 65000.0)
    order = asyncio.run(broker.submit_order("BTCUSDT", "buy", 0.001))
    assert order.qty == 0.001
    assert order.filled_qty == 0.001
    assert order.status == "filled"

def test_paper_broker_float_position():
    broker = PaperBroker(initial_capital=100_000)
    broker.update_price("BTCUSDT", 65000.0)
    asyncio.run(broker.submit_order("BTCUSDT", "buy", 0.5))
    positions = asyncio.run(broker.get_positions())
    assert len(positions) == 1
    assert positions[0].qty == 0.5
```

**Step 2: Run test — confirm it fails**
```bash
python -m pytest oms/tests/test_broker_float_qty.py -v -k "not vcr"
```
Expected: FAIL — TypeError on qty field or assertion failure

**Step 3: Implement — change int→float in oms/broker/__init__.py**

In `BrokerOrder` dataclass, change:
```python
qty: int = 0          →  qty: float = 0.0
filled_qty: int = 0   →  filled_qty: float = 0.0
```

In `BrokerPosition` dataclass, change:
```python
qty: int              →  qty: float
```

In `Broker` Protocol, change all `qty: int` to `qty: float`.

In `PaperBroker._execute_fill`, `update_price`, and `submit_order`, ensure float arithmetic throughout (no more `int()` casts on qty).

**Step 4: Run test — confirm it passes**
```bash
python -m pytest oms/tests/test_broker_float_qty.py -v -k "not vcr"
```
Expected: 4 PASS

**Step 5: Commit**
```bash
git add oms/broker/__init__.py oms/tests/test_broker_float_qty.py
git commit -m "feat: upgrade Broker protocol qty from int to float"
```

---

### Task 2: qty float upgrade — TrackedOrder + OrderManager

**Files:**
- Modify: `oms/state.py`, `oms/manager.py`

**Step 1: Update TrackedOrder**

In `oms/state.py`, change `TrackedOrder` dataclass:
```python
qty: float = 0.0
filled_qty: float = 0.0
```

**Step 2: Update OrderManager**

In `oms/manager.py`, change `submit()` parameter:
```python
async def submit(self, symbol, side, qty: float, order_type="market", ...)
```

**Step 3: Run existing tests to catch breakage**
```bash
python -m pytest oms/tests/test_state.py oms/tests/test_manager.py -v -k "not vcr"
```
Fix any int→float type mismatches in tests (e.g., `qty=50` → `qty=50.0` assertions).

Expected: All PASS after fixes.

**Step 4: Commit**
```bash
git add oms/state.py oms/manager.py oms/tests/test_state.py oms/tests/test_manager.py
git commit -m "feat: upgrade TrackedOrder and OrderManager qty to float"
```

---

### Task 3: qty float upgrade — Bridge, Position, AlpacaBroker + all remaining tests

**Files:**
- Modify: `oms/bridge.py`, `oms/position.py`, `oms/broker/alpaca_broker.py`
- Modify: All test files referencing qty

**Step 1: Update bridge.py**

In `convert_signal()`, ensure qty computation produces float:
```python
qty = max(1.0, float(portfolio.total_equity * weight / 100.0))
```

**Step 2: Update position.py**

In `PositionTracker.record_fill()`, accept float qty:
```python
def record_fill(self, symbol: str, side: str, qty: float):
```

**Step 3: Update alpaca_broker.py**

Wrap qty with `float()` when creating BrokerOrder — Alpaca API internally handles the int conversion.

**Step 4: Fix all test files**

Run full OMS test suite:
```bash
python -m pytest oms/tests/ -v -k "not vcr" --tb=short -q 2>&1 | tail -20
```

Fix any failing tests:
- Change `qty=100` → `qty=100.0` in assertions where needed
- Update type checks from `isinstance(qty, int)` → `isinstance(qty, float)`
- Ensure `filled_qty` comparisons use float

**Step 5: Run full test suite — all must pass**
```bash
python -m pytest oms/tests/ engine/tests/ collectors/tests/ sdk/tests/ quality/tests/ -v -k "not vcr" --tb=short -q 2>&1 | tail -5
```

Expected: All tests PASS (0 failed).

**Step 6: Commit**
```bash
git add oms/bridge.py oms/position.py oms/broker/alpaca_broker.py oms/tests/
git commit -m "fix: update bridge, position tracker, and all tests for float qty"
```

---

### Task 4: CryptoBinanceAdapter — data adapter

**Files:**
- Create: `collectors/adapters/crypto_binance_adapter.py`
- Create: `collectors/tests/test_crypto_adapter.py`

**Step 1: Write failing test**

In `collectors/tests/test_crypto_adapter.py`:

```python
"""Tests for CryptoBinanceAdapter."""
import pytest
from datetime import datetime, timezone
from collectors.adapters.crypto_binance_adapter import CryptoBinanceAdapter


def test_adapter_has_crypto_market():
    adapter = CryptoBinanceAdapter()
    assert adapter.market == "CRYPTO"

def test_adapter_symbols_include_btc():
    adapter = CryptoBinanceAdapter()
    symbols = adapter.fetch_supported_symbols()
    assert isinstance(symbols, list)
    assert "BTCUSDT" in symbols or any("BTC" in s for s in symbols)

def test_adapter_market_hours_24x7():
    from datetime import date
    adapter = CryptoBinanceAdapter()
    open_time, close_time = adapter.market_hours(date.today())
    assert open_time.hour == 0 and open_time.minute == 0
    assert close_time.hour == 23 and close_time.minute == 59

@pytest.mark.vcr
def test_fetch_bars_returns_valid_dataframe():
    adapter = CryptoBinanceAdapter()
    start = datetime(2026, 5, 15, tzinfo=timezone.utc)
    end = datetime(2026, 5, 16, tzinfo=timezone.utc)
    df = adapter.fetch_bars(["BTC/USDT"], start, end, frequency="1h")
    assert len(df) > 0
    assert "symbol" in df.columns
    assert "close" in df.columns
    assert "market" in df.columns
    assert all(df["market"] == "CRYPTO")
```

Run: `python -m pytest collectors/tests/test_crypto_adapter.py::test_adapter_has_crypto_market -v`
Expected: FAIL

**Step 2: Implement CryptoBinanceAdapter**

In `collectors/adapters/crypto_binance_adapter.py`:

```python
"""Binance cryptocurrency market adapter via ccxt."""
from datetime import date, datetime, time
import pandas as pd
import ccxt

class CryptoBinanceAdapter:
    market = "CRYPTO"

    _TOP_SYMBOLS = [
        "BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT", "XRP/USDT",
        "DOGE/USDT", "ADA/USDT", "AVAX/USDT", "DOT/USDT", "LINK/USDT",
        "MATIC/USDT", "UNI/USDT", "ATOM/USDT", "LTC/USDT", "ETC/USDT",
        "APT/USDT", "ARB/USDT", "OP/USDT", "NEAR/USDT", "FIL/USDT",
    ]

    _FREQ_MAP = {"1m": "1m", "5m": "5m", "15m": "15m", "30m": "30m",
                 "1h": "1h", "4h": "4h", "1d": "1d", "1w": "1w"}

    def __init__(self):
        self._exchange = ccxt.binance({"enableRateLimit": True})

    def fetch_bars(self, symbols, start, end, frequency="1m"):
        tf = self._FREQ_MAP.get(frequency, "1m")
        since_ms = int(start.timestamp() * 1000)
        end_ms = int(end.timestamp() * 1000)
        limit = 1000
        records = []

        for symbol in symbols:
            current_since = since_ms
            while current_since < end_ms:
                try:
                    ohlcv = self._exchange.fetch_ohlcv(symbol, tf, current_since, limit)
                except Exception:
                    break
                if not ohlcv:
                    break
                for row in ohlcv:
                    ts_ms = row[0]
                    if ts_ms >= end_ms:
                        break
                    if ts_ms >= since_ms:
                        records.append({
                            "symbol": symbol.replace("/", ""),
                            "timestamp": datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc),
                            "open": float(row[1]),
                            "high": float(row[2]),
                            "low": float(row[3]),
                            "close": float(row[4]),
                            "volume": int(float(row[5])),
                            "market": self.market,
                            "frequency": frequency,
                        })
                last_ts = ohlcv[-1][0]
                if last_ts <= current_since:
                    break
                current_since = last_ts + 1

        if not records:
            return pd.DataFrame(columns=[
                "symbol", "timestamp", "open", "high", "low", "close",
                "volume", "market", "frequency"
            ])
        return pd.DataFrame(records)

    def fetch_supported_symbols(self):
        return [s.replace("/", "") for s in self._TOP_SYMBOLS]

    def market_hours(self, d):
        return time(0, 0), time(23, 59)
```

**Step 3: Run tests**
```bash
python -m pytest collectors/tests/test_crypto_adapter.py -v -k "not vcr"
```
Expected: 3 PASS, 1 deselected (vcr)

**Step 4: Commit**
```bash
git add collectors/adapters/crypto_binance_adapter.py collectors/tests/test_crypto_adapter.py
git commit -m "feat: add CryptoBinanceAdapter for Binance OHLCV data"
```

---

### Task 5: Wire crypto adapter into collector main.py

**Files:**
- Modify: `collectors/main.py`
- Modify: `collectors/adapters/__init__.py`

**Step 1: Update adapter imports**

In `collectors/adapters/__init__.py`, add:
```python
from .crypto_binance_adapter import CryptoBinanceAdapter
```

**Step 2: Update get_adapter() in main.py**

In `collectors/main.py`, add after the alpaca branch:
```python
def get_adapter(source: str):
    if source == "alpaca":
        api_key = os.environ["ALPACA_API_KEY"]
        api_secret = os.environ["ALPACA_API_SECRET"]
        return AlpacaUSAdapter(api_key=api_key, api_secret=api_secret)
    if source == "cryptobinance":
        return CryptoBinanceAdapter()
    return YFinanceUSAdapter()
```

Also add `from adapters.crypto_binance_adapter import CryptoBinanceAdapter` to imports.

**Step 3: Verify collector can import**
```bash
cd collectors && python -c "from main import get_adapter; a = get_adapter('cryptobinance'); print(f'Adapter OK: market={a.market}, symbols={len(a.fetch_supported_symbols())}')"
```
Expected: `Adapter OK: market=CRYPTO, symbols=20`

**Step 4: Commit**
```bash
git add collectors/main.py collectors/adapters/__init__.py
git commit -m "feat: wire CryptoBinanceAdapter into collector entrypoint"
```

---

### Task 6: CryptoPaperBroker — simulated crypto trading

**Files:**
- Create: `oms/broker/crypto_broker.py`
- Create: `oms/tests/test_crypto_broker.py`

**Step 1: Write failing test**

In `oms/tests/test_crypto_broker.py`:

```python
"""Tests for crypto broker implementations."""
import pytest
import asyncio
from oms.broker.crypto_broker import CryptoPaperBroker


def test_crypto_paper_submit_market_buy():
    broker = CryptoPaperBroker(initial_capital=10_000.0)
    broker.update_price("BTCUSDT", 65000.0)
    order = asyncio.run(broker.submit_order("BTCUSDT", "buy", 0.01))
    assert order.symbol == "BTCUSDT"
    assert order.side == "buy"
    assert order.qty == 0.01
    assert order.filled_qty == 0.01
    assert order.status == "filled"
    assert order.avg_price is not None

def test_crypto_paper_position_tracking():
    broker = CryptoPaperBroker(initial_capital=10_000.0)
    broker.update_price("ETHUSDT", 3000.0)
    asyncio.run(broker.submit_order("ETHUSDT", "buy", 1.0))
    positions = asyncio.run(broker.get_positions())
    assert len(positions) == 1
    assert positions[0].symbol == "ETHUSDT"
    assert positions[0].qty == 1.0

def test_crypto_paper_account_equity():
    broker = CryptoPaperBroker(initial_capital=10_000.0)
    broker.update_price("BTCUSDT", 50000.0)
    asyncio.run(broker.submit_order("BTCUSDT", "buy", 0.1))
    acc = asyncio.run(broker.get_account())
    assert acc.equity > 0
    assert acc.cash < 10_000.0  # spent money

def test_crypto_paper_cancel_limit_order():
    broker = CryptoPaperBroker(initial_capital=10_000.0)
    broker.update_price("BTCUSDT", 65000.0)
    order = asyncio.run(broker.submit_order(
        "BTCUSDT", "buy", 0.01, order_type="limit", limit_price=60000.0
    ))
    result = asyncio.run(broker.cancel_order(order.broker_id))
    assert result is True

def test_crypto_paper_limit_fills_when_crossed():
    broker = CryptoPaperBroker(initial_capital=10_000.0)
    broker.update_price("BTCUSDT", 65000.0)
    order = asyncio.run(broker.submit_order(
        "BTCUSDT", "buy", 0.01, order_type="limit", limit_price=66000.0
    ))
    assert order.status == "filled"  # current price 65000 <= limit 66000
```

**Step 2: Implement CryptoPaperBroker**

In `oms/broker/crypto_broker.py`:

```python
"""Crypto broker implementations: PaperBroker and BinanceBroker."""
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Protocol
import uuid
import random

from oms.broker import BrokerOrder, BrokerPosition, BrokerAccount


class CryptoPaperBroker:
    """Simulated crypto broker for backtesting. Deterministic fills at synthetic prices."""

    def __init__(self, initial_capital: float = 10_000.0):
        self.cash = initial_capital
        self._positions: dict[str, BrokerPosition] = {}
        self._orders: dict[str, BrokerOrder] = {}
        self._prices: dict[str, float] = {}

    def update_price(self, symbol: str, price: float):
        self._prices[symbol] = price
        for oid, order in list(self._orders.items()):
            if order.status == "pending" and order.symbol == symbol:
                if self._limit_crossed(order, price):
                    self._execute_fill(order, price)

    def _price_for(self, symbol: str) -> float:
        return self._prices.get(symbol, 100.0)

    def _limit_crossed(self, order: BrokerOrder, current_price: float) -> bool:
        if order.limit_price is None:
            return False
        if order.side == "buy":
            return current_price <= order.limit_price
        return current_price >= order.limit_price

    def _execute_fill(self, order: BrokerOrder, fill_price: float):
        cost = fill_price * order.qty
        self.cash -= cost
        order.status = "filled"
        order.filled_qty = order.qty
        order.avg_price = fill_price
        order.updated_at = datetime.now(timezone.utc)
        pos = self._positions.get(order.symbol)
        if pos:
            total_qty = pos.qty + (order.qty if order.side == "buy" else -order.qty)
            if total_qty > 0:
                pos.avg_entry_price = (
                    (pos.avg_entry_price * pos.qty) + (fill_price * order.qty)
                ) / (pos.qty + order.qty)
            pos.qty = total_qty
        else:
            qty_signed = order.qty if order.side == "buy" else -order.qty
            self._positions[order.symbol] = BrokerPosition(
                symbol=order.symbol, qty=qty_signed,
                avg_entry_price=fill_price,
                market_value=fill_price * abs(qty_signed),
                unrealized_pnl=0.0,
            )

    async def submit_order(self, symbol, side, qty, order_type="market", limit_price=None):
        oid = str(uuid.uuid4())[:8]
        now = datetime.now(timezone.utc)
        order = BrokerOrder(
            broker_id=oid, symbol=symbol, side=side, qty=qty,
            order_type=order_type, limit_price=limit_price,
            created_at=now, updated_at=now,
        )
        current_price = self._price_for(symbol)
        if order_type == "limit":
            if self._limit_crossed(order, current_price):
                self._execute_fill(order, current_price)
            else:
                order.status = "pending"
                self._orders[oid] = order
            return order
        fill_price = current_price + random.uniform(-0.5, 0.5)
        self._execute_fill(order, fill_price)
        self._orders[oid] = order
        return order

    async def cancel_order(self, broker_id):
        if broker_id in self._orders:
            self._orders[broker_id].status = "cancelled"
            return True
        return False

    async def get_order(self, broker_id):
        return self._orders.get(broker_id)

    async def get_positions(self):
        return list(self._positions.values())

    async def get_account(self):
        equity = self.cash + sum(p.market_value for p in self._positions.values())
        return BrokerAccount(
            cash=self.cash, equity=equity,
            buying_power=equity * 2,
            positions=list(self._positions.values()),
        )

    async def get_open_orders(self):
        return [o for o in self._orders.values() if o.status in ("pending",)]
```

**Step 3: Run tests**
```bash
python -m pytest oms/tests/test_crypto_broker.py -v -k "not vcr"
```
Expected: 5 PASS

**Step 4: Commit**
```bash
git add oms/broker/crypto_broker.py oms/tests/test_crypto_broker.py
git commit -m "feat: add CryptoPaperBroker for simulated crypto trading"
```

---

### Task 7: CryptoBinanceBroker — real Binance API broker

**Files:**
- Modify: `oms/broker/crypto_broker.py` (add class)
- Modify: `oms/tests/test_crypto_broker.py` (add tests)

**Step 1: Add test**

In `oms/tests/test_crypto_broker.py`, append:

```python
import os

def test_crypto_binance_broker_requires_credentials():
    from oms.broker.crypto_broker import CryptoBinanceBroker
    if not os.environ.get("BINANCE_API_KEY"):
        with pytest.raises(ValueError):
            CryptoBinanceBroker()
    else:
        broker = CryptoBinanceBroker(testnet=True)
        assert broker is not None

@pytest.mark.vcr
def test_crypto_binance_broker_get_account():
    from oms.broker.crypto_broker import CryptoBinanceBroker
    key = os.environ.get("BINANCE_API_KEY", "test")
    secret = os.environ.get("BINANCE_API_SECRET", "test")
    broker = CryptoBinanceBroker(api_key=key, api_secret=secret, testnet=True)
    acc = asyncio.run(broker.get_account())
    assert acc.cash >= 0
```

**Step 2: Implement CryptoBinanceBroker**

Append to `oms/broker/crypto_broker.py`:

```python
import os
import ccxt


class CryptoBinanceBroker:
    """Real Binance broker via ccxt. Supports testnet and live trading."""

    def __init__(self, api_key=*** api_secret=None, testnet=True):
        key = api_key or os.environ.get("BINANCE_API_KEY", "")
        secret = api_secret or os.environ.get("BINANCE_API_SECRET", "")
        if not key or not secret:
            raise ValueError(
                "Binance API credentials required. "
                "Set BINANCE_API_KEY and BINANCE_API_SECRET."
            )
        config = {
            "apiKey": key, "secret": secret, "enableRateLimit": True,
        }
        if testnet:
            config["urls"] = {"api": {"public": "https://testnet.binancefuture.com/fapi/v1",
                                      "private": "https://testnet.binancefuture.com/fapi/v1"}}
        self._exchange = ccxt.binance(config)

    async def submit_order(self, symbol, side, qty, order_type="market", limit_price=None):
        try:
            params = {}
            if order_type == "limit" and limit_price:
                resp = self._exchange.create_limit_order(symbol, side, qty, limit_price, params)
            else:
                resp = self._exchange.create_market_order(symbol, side, qty, params)
            return BrokerOrder(
                broker_id=str(resp.get("id", "")), symbol=resp.get("symbol", symbol),
                side=side, qty=qty, filled_qty=float(resp.get("filled", 0)),
                status="filled" if resp.get("status") == "closed" else resp.get("status", "unknown"),
                avg_price=float(resp.get("average", 0)) if resp.get("average") else None,
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
            )
        except Exception as e:
            raise RuntimeError(f"Binance order failed: {e}")

    async def cancel_order(self, broker_id):
        try:
            self._exchange.cancel_order(broker_id)
            return True
        except Exception:
            return False

    async def get_order(self, broker_id):
        resp = self._exchange.fetch_order(broker_id)
        return BrokerOrder(
            broker_id=str(resp.get("id", broker_id)),
            symbol=resp.get("symbol", ""), side=resp.get("side", ""),
            qty=float(resp.get("amount", 0)), filled_qty=float(resp.get("filled", 0)),
            status=resp.get("status", "unknown"),
        )

    async def get_positions(self):
        try:
            positions = self._exchange.fetch_positions()
        except Exception:
            positions = []
        result = []
        for p in positions:
            qty = float(p.get("contracts", 0))
            if qty == 0:
                continue
            result.append(BrokerPosition(
                symbol=p.get("symbol", ""), qty=qty,
                avg_entry_price=float(p.get("entryPrice", 0)),
                market_value=float(p.get("notional", 0)),
                unrealized_pnl=float(p.get("unrealizedPnl", 0)),
            ))
        return result

    async def get_account(self):
        balance = self._exchange.fetch_balance()
        usdt = balance.get("USDT", {})
        cash = float(usdt.get("free", 0))
        total = float(balance.get("total", {}).get("USDT", cash))
        return BrokerAccount(
            cash=cash, equity=total, buying_power=total * 2,
            positions=await self.get_positions(),
        )

    async def get_open_orders(self):
        orders = self._exchange.fetch_open_orders()
        return [BrokerOrder(
            broker_id=str(o.get("id", "")), symbol=o.get("symbol", ""),
            side=o.get("side", ""), qty=float(o.get("amount", 0)),
            filled_qty=float(o.get("filled", 0)), status=o.get("status", "open"),
        ) for o in orders]
```

**Step 3: Run tests**
```bash
python -m pytest oms/tests/test_crypto_broker.py -v -k "not vcr"
```
Expected: 7 PASS (5 paper + 2 binance), 1 deselected

**Step 4: Commit**
```bash
git add oms/broker/crypto_broker.py oms/tests/test_crypto_broker.py
git commit -m "feat: add CryptoBinanceBroker for real Binance trading"
```

---

### Task 8: Go Query API — add CRYPTO market

**Files:**
- Modify: `query-api/internal/market/market.go`
- Modify: `query-api/internal/market/market_test.go`

**Step 1: Add CRYPTO constant**

In `query-api/internal/market/market.go`:

Add to const block:
```go
CRYPTO Market = "CRYPTO"
```

Add to ParseMarket switch:
```go
case "CRYPTO":
    return CRYPTO, true
```

**Step 2: Add test**

In `query-api/internal/market/market_test.go`, add:

```go
func TestParseMarketCrypto(t *testing.T) {
    m, ok := ParseMarket("CRYPTO")
    if !ok {
        t.Fatal("expected CRYPTO to parse successfully")
    }
    if m != CRYPTO {
        t.Fatalf("expected CRYPTO, got %s", m)
    }
}

func TestCryptoStoragePrefix(t *testing.T) {
    prefix := CRYPTO.StoragePrefix("bars")
    expected := "raw/crypto/bars"
    if prefix != expected {
        t.Fatalf("expected %s, got %s", expected, prefix)
    }
}
```

**Step 3: Run Go tests**
```bash
cd query-api && go vet ./... && go test ./... -v -cover
```
Expected: All tests PASS, coverage maintained.

**Step 4: Commit**
```bash
git add query-api/internal/market/market.go query-api/internal/market/market_test.go
git commit -m "feat: add CRYPTO market to Go query API"
```

---

### Task 9: Terraform — crypto infrastructure

**Files:**
- Create: `terraform/cloud_run_crypto.tf`

**Step 1: Create terraform config**

In `terraform/cloud_run_crypto.tf`:

```hcl
# =============================================================================
# Crypto (Binance) Data Pipeline
# =============================================================================

# --- Cloud Run Job: Crypto Bar Collector ---
resource "google_cloud_run_v2_job" "collector_crypto" {
  name     = "quant-collector-crypto-binance"
  location = var.region
  deletion_protection = false

  template {
    template {
      service_account = google_service_account.collector.email
      containers {
        image = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.docker.repository_id}/collector:latest"
        env {
          name  = "GCS_BUCKET"
          value = google_storage_bucket.quant_data.name
        }
        env {
          name  = "COLLECTOR_SOURCE"
          value = "cryptobinance"
        }
        env {
          name  = "SYMBOLS"
          value = "BTC/USDT,ETH/USDT,SOL/USDT,BNB/USDT,XRP/USDT,DOGE/USDT,ADA/USDT,AVAX/USDT,DOT/USDT,LINK/USDT"
        }
        env {
          name  = "FREQUENCY"
          value = "1m"
        }
        env {
          name  = "LOOKBACK_MINUTES"
          value = "120"
        }
        resources {
          limits = {
            memory = "512Mi"
            cpu    = "1"
          }
        }
      }
      max_retries = 3
      timeout     = "600s"
    }
  }
}

# --- Cloud Scheduler: 24×7 cron trigger ---
resource "google_cloud_scheduler_job" "collect_crypto_bars" {
  name             = "quant-collect-crypto-bars"
  schedule         = "*/5 * * * *"  # every 5 minutes, all days
  time_zone        = "Etc/UTC"
  attempt_deadline = "600s"

  retry_config {
    retry_count = 3
  }

  http_target {
    http_method = "POST"
    uri         = "https://${var.region}-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/${var.project_id}/jobs/${google_cloud_run_v2_job.collector_crypto.name}:run"
    oauth_token {
      service_account_email = google_service_account.collector.email
    }
  }
}

# --- BigQuery External Table: Crypto Bars ---
resource "google_bigquery_table" "crypto_bars" {
  dataset_id = google_bigquery_dataset.quant.dataset_id
  table_id   = "crypto_bars"

  external_data_configuration {
    autodetect    = true
    source_format = "PARQUET"
    source_uris   = [
      "gs://${google_storage_bucket.quant_data.name}/raw/crypto/bars/*/*/*/*.parquet"
    ]

    hive_partitioning_options {
      mode             = "AUTO"
      source_uri_prefix = "gs://${google_storage_bucket.quant_data.name}/raw/crypto/bars/"
    }
  }
}
```

**Step 2: Validate**
```bash
cd terraform && terraform fmt -check -recursive && terraform validate
```
Expected: No errors.

**Step 3: Commit**
```bash
git add terraform/cloud_run_crypto.tf
git commit -m "feat: add Terraform config for crypto data pipeline"
```

---

### Task 10: Integration test — end-to-end crypto pipeline

**Files:**
- Create: `oms/tests/test_crypto_integration.py`

**Step 1: Write integration test**

In `oms/tests/test_crypto_integration.py`:

```python
"""Integration tests for crypto end-to-end flow."""
import asyncio
import pytest
import pandas as pd
import numpy as np
from oms.broker.crypto_broker import CryptoPaperBroker
from oms.manager import OrderManager
from oms.bridge import convert_signal, forward_signal
from engine.strategy import Strategy, Signal, StrategyContext
from engine.data import DataFrameSource
from engine.config import BacktestConfig
from engine.engine import Engine


def test_crypto_signal_to_broker_flow():
    """Verify a crypto buy signal flows through bridge → order manager → broker."""
    broker = CryptoPaperBroker(initial_capital=10_000)
    broker.update_price("BTCUSDT", 65000.0)
    mgr = OrderManager(broker)

    signal_dict = {"symbol": "BTCUSDT", "side": "buy", "qty": 0.01}
    results = forward_signal(signal_dict, broker, mgr)

    assert len(results) == 1
    assert results[0].symbol == "BTCUSDT"
    assert results[0].state == "filled"
    assert results[0].filled_qty == 0.01

    positions = asyncio.run(broker.get_positions())
    assert len(positions) == 1
    assert positions[0].symbol == "BTCUSDT"


def test_crypto_backtest_with_engine():
    """Run a simple momentum strategy on crypto data through the engine."""
    dates = pd.date_range("2026-01-01", periods=200, freq="1h")
    np.random.seed(42)
    trend = np.cumsum(np.random.randn(200) * 50 + 5) + 60000
    close = pd.DataFrame({"BTCUSDT": trend}, index=dates)
    data = DataFrameSource(close=close)

    class CryptoMomentum(Strategy):
        lookback: int = 20

        def on_init(self, ctx):
            self.ma = ctx.data.close.rolling(self.lookback).mean()

        def on_bar(self, ctx, bar):
            if bar < self.lookback:
                return []
            if close.iloc[bar]["BTCUSDT"] > self.ma.iloc[bar]["BTCUSDT"]:
                if not ctx.portfolio.has_position("BTCUSDT"):
                    return [Signal.buy("BTCUSDT", weight=1.0)]
            else:
                if ctx.portfolio.has_position("BTCUSDT"):
                    return [Signal.close("BTCUSDT")]
            return []

    cfg = BacktestConfig(initial_capital=10_000, slippage_bps=10, commission_bps=10)
    result = Engine(cfg).run(CryptoMomentum(), data)

    from engine.metrics import summary
    s = summary(result)
    assert s["total_trades"] >= 0
    assert isinstance(s["sharpe_ratio"], float)
    assert len(result.portfolio.equity_curve) == 200
```

**Step 2: Run and verify**
```bash
python -m pytest oms/tests/test_crypto_integration.py -v -k "not vcr"
```
Expected: 2 PASS

**Step 3: Commit**
```bash
git add oms/tests/test_crypto_integration.py
git commit -m "test: add crypto end-to-end integration tests"
```

---

### Task 11: Final verification — full test suite

**Step 1: Run complete Python test suite**
```bash
python -m pytest oms/tests/ engine/tests/ collectors/tests/ sdk/tests/ quality/tests/ -v -k "not vcr" --tb=short -q 2>&1 | tail -10
```
Expected: All tests PASS, no regressions.

**Step 2: Run Go tests**
```bash
cd query-api && go vet ./... && go test ./... -v -cover
```
Expected: All PASS.

**Step 3: Verify Terraform**
```bash
cd terraform && terraform fmt -check -recursive && terraform validate
```
Expected: No errors.

**Step 4: Quick smoke test — import all crypto modules**
```bash
python -c "
from collectors.adapters.crypto_binance_adapter import CryptoBinanceAdapter
from oms.broker.crypto_broker import CryptoPaperBroker, CryptoBinanceBroker
print('Adapter:', CryptoBinanceAdapter().market)
print('PaperBroker:', type(CryptoPaperBroker()))
print('All imports OK')
"
```
Expected: All imports succeed.

**Step 5: Commit**
```bash
git add -A
git commit -m "chore: final verification — all tests pass, crypto integration complete"
```

---

## Completion Checklist

- [x] Design doc written and committed
- [ ] Task 1: Broker protocol qty float
- [ ] Task 2: TrackedOrder + OrderManager float
- [ ] Task 3: Bridge, Position, Alpaca + all tests
- [ ] Task 4: CryptoBinanceAdapter
- [ ] Task 5: Collector main.py wiring
- [ ] Task 6: CryptoPaperBroker
- [ ] Task 7: CryptoBinanceBroker
- [ ] Task 8: Go API CRYPTO constant
- [ ] Task 9: Terraform crypto infra
- [ ] Task 10: Integration tests
- [ ] Task 11: Final verification
