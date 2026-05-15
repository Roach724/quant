# Phase 3: Execution & OMS — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) to implement this plan task-by-task.

**Goal:** Build the execution layer — broker gateway (Alpaca paper + live), OMS with order lifecycle tracking, position reconciliation, TWAP/VWAP execution algos, and real-time market data streaming.

**Architecture:** `quant.broker` (Broker protocol, AlpacaBroker, PaperBroker), `quant.oms` (OrderManager, OrderState, PositionTracker), `quant.execution` (TWAPExecutor, VWAPExecutor). Async throughout (asyncio + alpaca-py).

**Tech Stack:** Python 3.12+, alpaca-py, asyncio, pytest, VCR.py.

---

## File Structure

```
oms/                              # quant.oms
├── __init__.py
├── broker/
│   ├── __init__.py               # Broker Protocol + PaperBroker
│   ├── alpaca_broker.py          # Alpaca REST + WS
│   └── market_data.py            # Real-time bar streaming
├── state.py                      # OrderState, TrackedOrder
├── manager.py                    # OrderManager
├── position.py                   # PositionTracker
execution/
├── __init__.py
├── protocol.py                   # ExecutionAlgo Protocol
├── twap.py                       # TWAP executor
└── vwap.py                       # VWAP executor
oms/tests/
├── __init__.py
├── conftest.py
├── test_broker.py
├── test_state.py
├── test_manager.py
├── test_position.py
├── test_twap.py
└── test_vwap.py
```

---

### Task 1: Scaffolding + Broker Protocol + PaperBroker

**Files:** `oms/__init__.py`, `oms/broker/__init__.py`, `oms/tests/conftest.py`, `oms/tests/test_broker.py`

- [ ] **Step 1: Create directory structure and write failing tests**

```bash
mkdir -p oms/broker oms/tests execution
touch oms/__init__.py oms/broker/__init__.py execution/__init__.py
```

```python
# oms/tests/test_broker.py
import pytest
import asyncio
from oms.broker import Broker, PaperBroker, BrokerOrder, BrokerPosition, BrokerAccount

def test_paper_broker_submit_order():
    broker = PaperBroker(initial_capital=100_000)
    order = asyncio.run(broker.submit_order("AAPL", "buy", 100))
    assert order.symbol == "AAPL"
    assert order.side == "buy"
    assert order.qty == 100
    assert order.status == "filled"
    assert order.avg_price is not None

def test_paper_broker_tracks_positions():
    broker = PaperBroker(initial_capital=100_000)
    asyncio.run(broker.submit_order("AAPL", "buy", 50))
    positions = asyncio.run(broker.get_positions())
    assert len(positions) == 1
    assert positions[0].symbol == "AAPL"
    assert positions[0].qty == 50

def test_paper_broker_account():
    broker = PaperBroker(initial_capital=50_000)
    acc = asyncio.run(broker.get_account())
    assert acc.cash == 50_000
    assert acc.equity == 50_000
    assert acc.buying_power > 0

def test_paper_broker_cancel_order():
    broker = PaperBroker(initial_capital=100_000)
    order = asyncio.run(broker.submit_order("AAPL", "buy", 100, order_type="limit", limit_price=100.0))
    # Paper limit orders are pending until price is right
    result = asyncio.run(broker.cancel_order(order.broker_id))
    assert result is True
```

Run: `cd oms && python -m pytest tests/test_broker.py -v`
Expected: FAIL

- [ ] **Step 2: Implement broker protocol and PaperBroker**

```python
# oms/broker/__init__.py
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Protocol, Literal
import uuid
import random

@dataclass
class BrokerOrder:
    broker_id: str
    symbol: str
    side: str
    qty: int
    filled_qty: int = 0
    status: str = "pending"
    avg_price: float | None = None
    order_type: str = "market"
    limit_price: float | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

@dataclass
class BrokerPosition:
    symbol: str
    qty: int
    avg_entry_price: float
    market_value: float
    unrealized_pnl: float

@dataclass
class BrokerAccount:
    cash: float
    equity: float
    buying_power: float
    positions: list[BrokerPosition] = field(default_factory=list)

class Broker(Protocol):
    async def submit_order(self, symbol: str, side: str, qty: int,
                           order_type: str = "market",
                           limit_price: float | None = None) -> BrokerOrder: ...
    async def cancel_order(self, broker_id: str) -> bool: ...
    async def get_order(self, broker_id: str) -> BrokerOrder: ...
    async def get_positions(self) -> list[BrokerPosition]: ...
    async def get_account(self) -> BrokerAccount: ...
    async def get_open_orders(self) -> list[BrokerOrder]: ...


class PaperBroker:
    """Simulated broker. No network calls. Deterministic fills at synthetic prices."""

    def __init__(self, initial_capital: float = 100_000.0):
        self.cash = initial_capital
        self._positions: dict[str, BrokerPosition] = {}
        self._orders: dict[str, BrokerOrder] = {}
        self._price = 100.0

    async def submit_order(self, symbol, side, qty, order_type="market", limit_price=None):
        oid = str(uuid.uuid4())[:8]
        now = datetime.now(timezone.utc)
        order = BrokerOrder(broker_id=oid, symbol=symbol, side=side, qty=qty,
                            order_type=order_type, limit_price=limit_price,
                            created_at=now, updated_at=now)
        if order_type == "limit":
            order.status = "pending"
            self._orders[oid] = order
            return order

        self._price += random.uniform(-0.5, 0.5)
        fill_price = self._price
        cost = fill_price * qty
        self.cash -= cost

        order.status = "filled"
        order.filled_qty = qty
        order.avg_price = fill_price
        order.updated_at = datetime.now(timezone.utc)

        pos = self._positions.get(symbol)
        if pos:
            total_qty = pos.qty + (qty if side == "buy" else -qty)
            avg_price = ((pos.avg_entry_price * pos.qty) + (fill_price * qty)) / (pos.qty + qty) if total_qty > 0 else fill_price
            pos.qty = total_qty
            pos.avg_entry_price = avg_price
        else:
            qty_signed = qty if side == "buy" else -qty
            self._positions[symbol] = BrokerPosition(symbol=symbol, qty=qty_signed,
                avg_entry_price=fill_price, market_value=fill_price*abs(qty_signed),
                unrealized_pnl=0.0)
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
        return BrokerAccount(cash=self.cash, equity=equity,
                             buying_power=equity*2, positions=self.get_positions())

    async def get_open_orders(self):
        return [o for o in self._orders.values() if o.status in ("pending",)]
```

Run: `cd oms && python -m pytest tests/test_broker.py -v`
Expected: 4 PASS

- [ ] **Step 3: Commit**

```bash
git add oms/
git commit -m "feat: add Broker protocol and PaperBroker for simulated trading"
```

---

### Task 2: AlpacaBroker Implementation

**Files:** `oms/broker/alpaca_broker.py`, `oms/tests/test_broker.py` (add tests)

- [ ] **Step 1: Write test for AlpacaBroker**

Add to `oms/tests/test_broker.py`:
```python
import os

def test_alpaca_broker_requires_credentials():
    from oms.broker.alpaca_broker import AlpacaBroker
    # Should raise if neither env vars nor explicit keys provided
    if not os.environ.get("ALPACA_API_KEY"):
        with pytest.raises(ValueError):
            AlpacaBroker()
    else:
        broker = AlpacaBroker()
        assert broker is not None

@pytest.mark.vcr
def test_alpaca_broker_get_account():
    from oms.broker.alpaca_broker import AlpacaBroker
    key = os.environ.get("ALPACA_API_KEY", "test")
    secret = os.environ.get("ALPACA_API_SECRET", "test")
    broker = AlpacaBroker(api_key=key, api_secret=secret, paper=True)
    acc = asyncio.run(broker.get_account())
    assert acc.cash >= 0
    assert acc.equity > 0
```

- [ ] **Step 2: Implement AlpacaBroker**

```python
# oms/broker/alpaca_broker.py
import os
from datetime import datetime, timezone
from alpaca.trading.client import TradingClient
from oms.broker import BrokerOrder, BrokerPosition, BrokerAccount

class AlpacaBroker:
    def __init__(self, api_key=None, api_secret=None, paper=True):
        key = api_key or os.environ.get("ALPACA_API_KEY")
        secret = api_secret or os.environ.get("ALPACA_API_SECRET")
        if not key or not secret:
            raise ValueError("Alpaca API credentials required. Set ALPACA_API_KEY and ALPACA_API_SECRET.")
        self._client = TradingClient(key, secret, paper=paper)

    async def submit_order(self, symbol, side, qty, order_type="market", limit_price=None):
        from alpaca.trading.requests import MarketOrderRequest, LimitOrderRequest
        from alpaca.trading.enums import OrderSide, TimeInForce
        side_enum = OrderSide.BUY if side == "buy" else OrderSide.SELL
        if order_type == "limit" and limit_price:
            req = LimitOrderRequest(symbol=symbol, qty=qty, side=side_enum,
                                    limit_price=limit_price, time_in_force=TimeInForce.DAY)
        else:
            req = MarketOrderRequest(symbol=symbol, qty=qty, side=side_enum,
                                     time_in_force=TimeInForce.DAY)
        resp = self._client.submit_order(req)
        return BrokerOrder(broker_id=str(resp.id), symbol=resp.symbol, side=side,
                           qty=int(resp.qty), filled_qty=int(resp.filled_qty),
                           status=resp.status, avg_price=float(resp.filled_avg_price) if resp.filled_avg_price else None,
                           created_at=resp.created_at, updated_at=resp.updated_at)

    async def cancel_order(self, broker_id):
        try:
            self._client.cancel_order_by_id(broker_id)
            return True
        except Exception:
            return False

    async def get_order(self, broker_id):
        resp = self._client.get_order_by_id(broker_id)
        return BrokerOrder(broker_id=str(resp.id), symbol=resp.symbol, side=resp.side,
                           qty=int(resp.qty), filled_qty=int(resp.filled_qty),
                           status=resp.status, avg_price=float(resp.filled_avg_price) if resp.filled_avg_price else None)

    async def get_positions(self):
        positions = self._client.get_all_positions()
        return [BrokerPosition(symbol=p.symbol, qty=int(p.qty),
                avg_entry_price=float(p.avg_entry_price),
                market_value=float(p.market_value),
                unrealized_pnl=float(p.unrealized_pl)) for p in positions]

    async def get_account(self):
        acc = self._client.get_account()
        return BrokerAccount(cash=float(acc.cash), equity=float(acc.equity),
                             buying_power=float(acc.buying_power))

    async def get_open_orders(self):
        orders = self._client.get_orders()
        return [BrokerOrder(broker_id=str(o.id), symbol=o.symbol, side=o.side,
                qty=int(o.qty), filled_qty=int(o.filled_qty), status=o.status) for o in orders]
```

- [ ] **Step 3: Update oms/broker/__init__.py** — add `from .alpaca_broker import AlpacaBroker`

- [ ] **Step 4: Commit**

```bash
git add oms/broker/
git commit -m "feat: add AlpacaBroker with paper/live support"
```

---

### Task 3: OrderManager & OrderState

**Files:** `oms/state.py`, `oms/manager.py`, `oms/tests/test_state.py`, `oms/tests/test_manager.py`

- [ ] **Step 1: Implement state.py and manager.py**

```python
# oms/state.py
from dataclasses import dataclass, field
from datetime import datetime, timezone
import uuid

VALID_STATES = {"PENDING", "SUBMITTED", "ACKNOWLEDGED", "PARTIAL_FILL", "FILLED", "CANCELLED", "REJECTED"}

@dataclass
class TrackedOrder:
    internal_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    broker_id: str | None = None
    symbol: str = ""
    side: str = ""
    qty: int = 0
    filled_qty: int = 0
    state: str = "PENDING"
    avg_fill_price: float | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    strategy_name: str = ""
    signal_id: str | None = None

    def transition(self, new_state: str):
        if new_state not in VALID_STATES:
            raise ValueError(f"Invalid state: {new_state}")
        self.state = new_state
        self.updated_at = datetime.now(timezone.utc)
```

```python
# oms/manager.py
from datetime import datetime
from oms.state import TrackedOrder

class OrderManager:
    def __init__(self, broker):
        self.broker = broker
        self.orders: dict[str, TrackedOrder] = {}
        self._history: list[TrackedOrder] = []

    async def submit(self, symbol, side, qty, order_type="market",
                     strategy_name="", signal_id=None, limit_price=None) -> TrackedOrder:
        t = TrackedOrder(symbol=symbol, side=side, qty=qty,
                         strategy_name=strategy_name, signal_id=signal_id)
        t.transition("SUBMITTED")
        try:
            broker_order = await self.broker.submit_order(symbol, side, qty, order_type, limit_price)
            t.broker_id = broker_order.broker_id
            t.state = broker_order.status.upper().replace(" ", "_")
            t.filled_qty = broker_order.filled_qty
            t.avg_fill_price = broker_order.avg_price
            t.updated_at = datetime.now()
        except Exception:
            t.transition("REJECTED")
        self.orders[t.internal_id] = t
        self._history.append(t)
        return t

    async def cancel(self, internal_id):
        t = self.orders.get(internal_id)
        if t and t.broker_id:
            ok = await self.broker.cancel_order(t.broker_id)
            if ok:
                t.transition("CANCELLED")
            return ok
        return False

    def get_open_orders(self):
        return [o for o in self.orders.values() if o.state in ("PENDING","SUBMITTED","ACKNOWLEDGED","PARTIAL_FILL")]

    def get_order_history(self, since=None):
        if since:
            return [o for o in self._history if o.created_at >= since]
        return list(self._history)
```

- [ ] **Step 2: Write tests**

```python
# oms/tests/test_state.py
from oms.state import TrackedOrder

def test_order_transitions():
    t = TrackedOrder(symbol="AAPL", side="buy", qty=100)
    assert t.state == "PENDING"
    t.transition("SUBMITTED")
    assert t.state == "SUBMITTED"
    t.transition("FILLED")
    assert t.state == "FILLED"
```

```python
# oms/tests/test_manager.py
import pytest, asyncio
from oms.manager import OrderManager
from oms.broker import PaperBroker

def test_submit_via_paper_broker():
    broker = PaperBroker(100_000)
    mgr = OrderManager(broker)
    t = asyncio.run(mgr.submit("AAPL", "buy", 50))
    assert t.state == "filled"
    assert t.broker_id is not None
    assert t.filled_qty == 50

def test_cancel_order():
    broker = PaperBroker(100_000)
    mgr = OrderManager(broker)
    t = asyncio.run(mgr.submit("AAPL", "buy", 100, order_type="limit", limit_price=1.0))
    ok = asyncio.run(mgr.cancel(t.internal_id))
    assert ok is True

def test_get_open_orders():
    broker = PaperBroker(100_000)
    mgr = OrderManager(broker)
    asyncio.run(mgr.submit("AAPL", "buy", 100, order_type="limit", limit_price=1.0))
    assert len(mgr.get_open_orders()) == 1
```

Run: `cd oms && python -m pytest tests/test_state.py tests/test_manager.py -v`
Expected: 4 PASS

- [ ] **Step 3: Commit**

```bash
git add oms/state.py oms/manager.py oms/tests/test_state.py oms/tests/test_manager.py
git commit -m "feat: add OrderManager with order lifecycle state machine"
```

---

### Task 4: PositionTracker

**Files:** `oms/position.py`, `oms/tests/test_position.py`

- [ ] **Step 1: Implement + test in one pass**

```python
# oms/position.py
class PositionTracker:
    def __init__(self, broker):
        self.broker = broker
        self._positions: dict[str, float] = {}

    def record_fill(self, symbol, side, qty):
        delta = qty if side == "buy" else -qty
        self._positions[symbol] = self._positions.get(symbol, 0) + delta

    async def reconcile(self):
        broker_pos = await self.broker.get_positions()
        issues = []
        broker_map = {p.symbol: p.qty for p in broker_pos}
        for sym, local_qty in self._positions.items():
            bq = broker_map.get(sym, 0)
            if local_qty != bq:
                issues.append(f"{sym}: local={local_qty}, broker={bq}")
        for sym in broker_map:
            if sym not in self._positions:
                issues.append(f"{sym}: missing local, broker={broker_map[sym]}")
        return issues

    @property
    def positions(self):
        return dict(self._positions)
```

```python
# oms/tests/test_position.py
import pytest, asyncio
from oms.position import PositionTracker
from oms.broker import PaperBroker

def test_record_and_reconcile():
    broker = PaperBroker(100_000)
    pt = PositionTracker(broker)
    pt.record_fill("AAPL", "buy", 100)
    pt.record_fill("AAPL", "sell", 30)
    assert pt.positions["AAPL"] == 70

def test_reconcile_no_broker_positions():
    broker = PaperBroker(100_000)
    pt = PositionTracker(broker)
    pt.record_fill("AAPL", "buy", 50)
    issues = asyncio.run(pt.reconcile())
    assert len(issues) > 0
```

Run: `cd oms && python -m pytest tests/test_position.py -v`
Expected: 2 PASS

- [ ] **Step 2: Commit**

```bash
git add oms/position.py oms/tests/test_position.py
git commit -m "feat: add PositionTracker with broker reconciliation"
```

---

### Task 5: Execution Algorithms (TWAP + VWAP)

**Files:** `execution/__init__.py`, `execution/protocol.py`, `execution/twap.py`, `execution/vwap.py`, `oms/tests/test_twap.py`, `oms/tests/test_vwap.py`

- [ ] **Step 1: Implement execution algos**

```python
# execution/protocol.py
from typing import Protocol

class ExecutionAlgo(Protocol):
    async def run(self, signal, broker, market_data) -> list:
        ...
```

```python
# execution/twap.py
import asyncio, random, uuid
from datetime import datetime, timezone
from oms.broker import BrokerOrder

class TWAPExecutor:
    def __init__(self, window_seconds: int = 1800, slices: int = 10, randomize: bool = True):
        self.window = window_seconds
        self.slices = slices
        self.randomize = randomize

    async def run(self, signal, broker, market_data):
        qty_per_slice = max(1, signal.get("qty", 100) // self.slices)
        interval = self.window / self.slices
        orders = []
        for i in range(self.slices):
            order = await broker.submit_order(signal.get("symbol","AAPL"),
                                               signal.get("side","buy"), qty_per_slice)
            orders.append(order)
            if self.randomize:
                jitter = random.uniform(-0.15, 0.15) * interval
            else:
                jitter = 0
            await asyncio.sleep(interval + jitter)
        return orders
```

```python
# execution/vwap.py
import asyncio
import numpy as np

class VWAPExecutor:
    def __init__(self, window_seconds: int = 1800, slices: int = 10, volume_profile=None):
        self.window = window_seconds
        self.slices = slices
        self.volume_profile = volume_profile

    async def run(self, signal, broker, market_data):
        qty = signal.get("qty", 100)
        symbol = signal.get("symbol", "AAPL")
        side = signal.get("side", "buy")
        interval = self.window / self.slices

        if self.volume_profile is not None and len(self.volume_profile) > 0:
            weights = self.volume_profile / self.volume_profile.sum()
            slice_qtys = [max(1, int(qty * w)) for w in weights[:self.slices]]
        else:
            slice_qtys = [max(1, qty // self.slices)] * self.slices

        orders = []
        for sq in slice_qtys:
            order = await broker.submit_order(symbol, side, sq)
            orders.append(order)
            await asyncio.sleep(interval)
        return orders
```

- [ ] **Step 2: Write tests**

```python
# oms/tests/test_twap.py
import pytest, asyncio
from execution.twap import TWAPExecutor
from oms.broker import PaperBroker

def test_twap_executes():
    broker = PaperBroker(100_000)
    twap = TWAPExecutor(window_seconds=2, slices=3, randomize=False)
    orders = asyncio.run(twap.run({"symbol":"AAPL","side":"buy","qty":90}, broker, None))
    assert len(orders) == 3
    assert all(o.status == "filled" for o in orders)
    # Each slice roughly equal
    assert orders[0].qty >= 25

def test_twap_randomize():
    broker = PaperBroker(100_000)
    twap = TWAPExecutor(window_seconds=1, slices=2, randomize=True)
    orders = asyncio.run(twap.run({"symbol":"MSFT","side":"sell","qty":50}, broker, None))
    assert len(orders) == 2
```

```python
# oms/tests/test_vwap.py
import pytest, asyncio
import numpy as np
from execution.vwap import VWAPExecutor
from oms.broker import PaperBroker

def test_vwap_equal_without_profile():
    broker = PaperBroker(100_000)
    vwap = VWAPExecutor(window_seconds=1, slices=4)
    orders = asyncio.run(vwap.run({"symbol":"AAPL","side":"buy","qty":100}, broker, None))
    assert len(orders) == 4
    assert all(o.status == "filled" for o in orders)

def test_vwap_with_profile():
    broker = PaperBroker(100_000)
    profile = np.array([0.1, 0.2, 0.3, 0.4])
    vwap = VWAPExecutor(window_seconds=1, slices=4, volume_profile=profile)
    orders = asyncio.run(vwap.run({"symbol":"AAPL","side":"buy","qty":100}, broker, None))
    assert orders[-1].qty > orders[0].qty  # larger slice at end
```

Run: `cd oms && python -m pytest tests/test_twap.py tests/test_vwap.py -v`
Expected: 4 PASS

- [ ] **Step 3: Commit**

```bash
git add execution/ oms/tests/test_twap.py oms/tests/test_vwap.py
git commit -m "feat: add TWAP and VWAP execution algorithms"
```

---

### Task 6: MarketDataStream

**Files:** `oms/broker/market_data.py`, `oms/tests/test_marketdata.py`

- [ ] **Step 1: Implement**

```python
# oms/broker/market_data.py
import asyncio
from typing import Callable

class MarketDataStream:
    """Real-time market data abstraction. Phase 3: base class with callback registry.
    Phase 3b: WebSocket connection to Alpaca/IB."""

    def __init__(self):
        self._bar_callbacks: list[Callable] = []
        self._connected = False

    async def connect(self, symbols):
        self._connected = True

    async def disconnect(self):
        self._connected = False

    def on_bar(self, callback):
        self._bar_callbacks.append(callback)

    async def latest_bar(self, symbol):
        return {"symbol": symbol, "close": 100.0, "timestamp": None}
```

Test: verify callback registration and connection state.

- [ ] **Step 2: Commit**

```bash
git add oms/broker/market_data.py oms/tests/test_marketdata.py
git commit -m "feat: add MarketDataStream base class"
```

---

### Task 7: Public API & Integration Test

**Files:** `oms/__init__.py`, `oms/tests/test_integration.py`

- [ ] **Step 1: Update __init__.py**

```python
from oms.broker import Broker, BrokerOrder, BrokerPosition, BrokerAccount, PaperBroker
from oms.broker.alpaca_broker import AlpacaBroker
from oms.state import TrackedOrder
from oms.manager import OrderManager
from oms.position import PositionTracker
from execution.twap import TWAPExecutor
from execution.vwap import VWAPExecutor
```

- [ ] **Step 2: Write integration test**

```python
# oms/tests/test_integration.py
import pytest, asyncio
from oms.broker import PaperBroker
from oms.manager import OrderManager
from oms.position import PositionTracker
from execution.twap import TWAPExecutor

def test_signal_to_position_flow():
    broker = PaperBroker(100_000)
    mgr = OrderManager(broker)
    pt = PositionTracker(broker)

    # Simulate strategy signal
    signal = {"symbol": "AAPL", "side": "buy", "qty": 100}

    # TWAP execution
    twap = TWAPExecutor(window_seconds=1, slices=4, randomize=False)
    orders = asyncio.run(twap.run(signal, broker, None))

    for o in orders:
        pt.record_fill(o.symbol, o.side, o.qty)
        # Also register in order manager
        t = asyncio.run(mgr.submit(o.symbol, o.side, o.qty))

    assert pt.positions["AAPL"] == 100
    assert len(orders) == 4
    assert all(o.filled_qty > 0 for o in orders)

def test_full_lifecycle():
    broker = PaperBroker(100_000)
    mgr = OrderManager(broker)

    t = asyncio.run(mgr.submit("MSFT", "buy", 50))
    assert t.internal_id is not None
    assert t.state == "filled"
    assert t.avg_fill_price is not None

    ok = asyncio.run(mgr.cancel(t.internal_id))
    assert ok is False  # Already filled, can't cancel
```

Run: all tests → `cd oms && python -m pytest tests/ -v`
Expected: ~15 PASS

- [ ] **Step 3: Commit**

```bash
git add oms/__init__.py oms/tests/test_integration.py
git commit -m "feat: add OMS integration test and public API"
```

---

### Task 8: Final Verification

- [ ] **Step 1: Run full test suite**

```bash
cd D:/quant && python -m pytest oms/tests/ engine/tests/ collectors/tests/ sdk/tests/ quality/tests/ -q -k "not vcr"
```

Expected: 62 + ~15 OMS = ~77 PASS

- [ ] **Step 2: Verify imports**

```python
from oms import PaperBroker, OrderManager, PositionTracker, TWAPExecutor, VWAPExecutor
print("All imports OK")
```
