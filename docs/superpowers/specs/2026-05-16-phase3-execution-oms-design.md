# Phase 3: Execution & OMS — Design Spec

**Date:** 2026-05-16
**Status:** Approved
**Scope:** Phase 3 of multi-phase quant trading system

## Overview

Build the execution layer that connects our Phase 2 backtesting engine to real broker APIs. This phase delivers an Order Management System (OMS), broker gateway with Alpaca integration, execution algorithms (TWAP, VWAP), and real-time market data streaming.

**Tech stack:** Python 3.12+, asyncio, websockets, alpaca-py. Runs on Cloud Run (stateless API) + GKE/Compute Engine (stateful execution server).

**Design principle:** Clean separation between broker protocol (what we do), OMS (how we track it), and execution algos (how we size/schedule orders). Paper trading first, live with confirmation.

## Architecture

```
                       ┌──────────────────────┐
                       │   quant.engine (P2)  │
                       │   Strategy → Signal  │
                       └──────────┬───────────┘
                                  │ Signal
                       ┌──────────▼───────────┐
                       │   quant.execution    │  TWAP, VWAP
                       │   Signal → Orders    │  ExecutionAlgo Protocol
                       └──────────┬───────────┘
                                  │ Order
                       ┌──────────▼───────────┐
                       │   quant.oms          │  OrderStateMachine
                       │   Order tracking     │  PositionTracker
                       │   Fill reconciliation│  Reconciliation
                       └──────────┬───────────┘
                                  │ REST + WS
                       ┌──────────▼───────────┐
                       │   quant.broker       │  Broker Protocol
                       │   AlpacaBroker       │  PaperBroker
                       │   MarketDataStream   │
                       └──────────┬───────────┘
                                  │
                       ┌──────────▼───────────┐
                       │   Alpaca API         │
                       │   (paper / live)     │
                       └──────────────────────┘
```

## Package Structure

```
oms/                              # quant.oms package
├── __init__.py
├── broker/
│   ├── __init__.py               # Broker Protocol
│   ├── alpaca_broker.py          # Alpaca REST + WebSocket
│   ├── paper_broker.py           # Simulated broker (no API needed)
│   └── market_data.py            # Real-time bar streaming
├── state.py                      # OrderState, OrderLifecycle
├── manager.py                    # OrderManager, submit/cancel/track
└── position.py                   # PositionTracker, reconciliation

execution/                        # quant.execution package
├── __init__.py
├── protocol.py                   # ExecutionAlgo Protocol
├── twap.py                       # TWAP executor
└── vwap.py                       # VWAP executor

oms/tests/
├── test_broker.py
├── test_state.py
├── test_manager.py
├── test_position.py
├── test_twap.py
├── test_vwap.py
└── conftest.py                   # VCR fixtures for Alpaca API
```

## Broker Protocol

```python
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, Literal

@dataclass
class BrokerOrder:
    """Order as understood by the broker."""
    broker_id: str
    symbol: str
    side: Literal["buy", "sell"]
    qty: int
    filled_qty: int
    status: str  # "pending", "filled", "partial_fill", "cancelled", "rejected"
    avg_price: float | None
    created_at: datetime
    updated_at: datetime

@dataclass
class BrokerPosition:
    symbol: str
    qty: int
    avg_entry_price: float
    market_value: float
    unrealized_pnl: float

@dataclass
class BrokerAccount:
    """Account summary from the broker."""
    cash: float
    equity: float
    buying_power: float
    positions: list[BrokerPosition]

class Broker(Protocol):
    """Abstract broker gateway. Concrete implementations connect to real APIs."""

    async def submit_order(self, symbol: str, side: str, qty: int,
                           order_type: str = "market",
                           limit_price: float | None = None) -> BrokerOrder: ...

    async def cancel_order(self, broker_id: str) -> bool: ...

    async def get_order(self, broker_id: str) -> BrokerOrder: ...

    async def get_positions(self) -> list[BrokerPosition]: ...

    async def get_account(self) -> BrokerAccount: ...

    async def get_open_orders(self) -> list[BrokerOrder]: ...
```

**Concrete implementations:**

- `AlpacaBroker(key, secret, paper=True)` — wraps alpaca-py `TradingClient`. Paper mode uses different base URL; API calls are identical. Uses asyncio throughout (alpaca-py is async-native).
- `PaperBroker(initial_capital)` — self-contained simulated broker. Fills orders instantly at current mid-price. No network calls. Useful for testing the OMS without Alpaca credentials.

## Order Management System (OMS)

**OrderState lifecycle:**

```
PENDING ──→ SUBMITTED ──→ ACKNOWLEDGED ──→ PARTIAL_FILL ──→ FILLED
   │            │               │
   └────────────┴───────────────┴──→ CANCELLED
   │
   └──→ REJECTED
```

```python
@dataclass
class TrackedOrder:
    """Internal OMS order with full lifecycle tracking."""
    internal_id: str          # UUID, assigned at creation
    broker_id: str | None     # Assigned by broker on submit
    symbol: str
    side: str
    qty: int
    filled_qty: int
    state: str                # One of the lifecycle states
    avg_fill_price: float | None
    created_at: datetime
    updated_at: datetime
    fills: list[Fill]         # Individual fill events
    strategy_name: str        # Which strategy generated this order
    signal_id: str | None     # Link back to the engine Signal
```

**OrderManager:**

```python
class OrderManager:
    """Central order tracking. All order operations go through here."""

    def __init__(self, broker: Broker):
        self.broker = broker
        self.orders: dict[str, TrackedOrder] = {}

    async def submit(self, symbol, side, qty, order_type="market",
                     strategy_name="", **kwargs) -> TrackedOrder:
        """Submit to broker, track internally. Returns the tracked order."""

    async def cancel(self, internal_id: str) -> bool: ...

    async def sync_from_broker(self):
        """Pull open orders from broker, reconcile with internal state."""

    def get_open_orders(self) -> list[TrackedOrder]: ...

    def get_order_history(self, since: datetime) -> list[TrackedOrder]: ...
```

## Position Tracking & Reconciliation

```python
class PositionTracker:
    """Tracks positions locally and reconciles with broker."""

    def __init__(self, broker: Broker):
        self.broker = broker
        self.local_positions: dict[str, float] = {}  # symbol → qty

    def record_fill(self, symbol: str, side: str, qty: int):
        """Update local position after a fill."""
        delta = qty if side == "buy" else -qty
        self.local_positions[symbol] = self.local_positions.get(symbol, 0) + delta

    async def reconcile(self) -> list[str]:
        """Compare local positions to broker positions. Return list of discrepancies."""
        broker_pos = await self.broker.get_positions()
        issues = []
        broker_map = {p.symbol: p.qty for p in broker_pos}
        for sym, local_qty in self.local_positions.items():
            broker_qty = broker_map.get(sym, 0)
            if local_qty != broker_qty:
                issues.append(f"{sym}: local={local_qty}, broker={broker_qty}")
        for sym in broker_map:
            if sym not in self.local_positions:
                issues.append(f"{sym}: missing from local, broker={broker_map[sym]}")
        return issues
```

## Execution Algorithms

```python
class ExecutionAlgo(Protocol):
    """Takes a large Signal, breaks it into child orders over time."""

    async def run(self, signal: Signal, broker: Broker,
                  market_data: MarketDataStream) -> list[BrokerOrder]: ...


class TWAPExecutor:
    """Time-Weighted Average Price.

    Slices a parent order into N equal-sized child orders,
    submitted at regular intervals over a time window.
    """

    def __init__(self, window_minutes: int = 30, slices: int = 10,
                 randomize: bool = True):
        self.window = window_minutes
        self.slices = slices
        self.randomize = randomize

    async def run(self, signal, broker, market_data) -> list[BrokerOrder]:
        qty_per_slice = max(1, signal.size // self.slices)
        interval = (self.window * 60) / self.slices
        orders = []
        for i in range(self.slices):
            await broker.submit_order(signal.symbol, signal.side, qty_per_slice)
            await asyncio.sleep(interval + random.uniform(-0.1, 0.1) * interval
                                if self.randomize else interval)
            orders.append(...)
        return orders


class VWAPExecutor:
    """Volume-Weighted Average Price.

    Slices using a historical volume profile to weight each slice.
    """

    def __init__(self, window_minutes: int = 30, volume_profile: pd.Series | None = None):
        self.window = window_minutes
        self.volume_profile = volume_profile  # Optional custom profile

    async def run(self, signal, broker, market_data) -> list[BrokerOrder]:
        # Distribute qty proportional to typical volume at each time slice
        ...
```

## Market Data Streaming

```python
class MarketDataStream:
    """Real-time market data via broker WebSocket.

    Subscribes to bars/quotes for a watchlist of symbols.
    Callbacks are registered for data events.
    """

    async def connect(self, symbols: list[str]): ...
    async def disconnect(self): ...

    def on_bar(self, callback: Callable): ...
    def on_quote(self, callback: Callable): ...

    async def latest_bar(self, symbol: str) -> dict: ...
```

For Alpaca: uses `alpaca-py`'s `CryptoDataStream` or `StockDataStream` for real-time WebSocket data. Abstracted behind this class so we can swap to IB, Polygon, or other providers later.

## Testing Strategy

- **Unit tests:** Each OMS component in isolation — `OrderManager` with a mock `Broker`, `PositionTracker` with synthetic fill data, `TWAPExecutor` with a fake broker. No network required.
- **Integration tests:** `AlpacaBroker` integration tests using VCR.py cassettes (record real API responses once, replay offline). PaperBroker is deterministic — test full signal→order→fill→position flow.
- **End-to-end test:** Full pipeline: Strategy generates signal → TWAP slices → OMS submits → Broker fills → PositionTracker reconciles. Runs against `PaperBroker` in unit tests, against `AlpacaBroker(paper=True)` in integration tests.

## Explicit Deferrals (out of scope)

- Interactive Brokers / CTP integration (Phase 3b)
- GKE deployment for stateful execution server (Phase 3b)
- Real-time risk checks at order submission (Phase 3b)
- Order book / Level 2 data (Phase 4)
- Multi-account support
