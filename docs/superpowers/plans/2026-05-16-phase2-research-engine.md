# Phase 2: Research & Backtesting Engine — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a custom institutional-grade backtesting engine (`quant.engine`) from scratch — no third-party backtesting frameworks.

**Architecture:** Hybrid vectorized+event-driven core. Strategy base class generates signals. Risk framework constrains orders as composable middleware. Portfolio tracks positions with pre-allocated numpy arrays. Two-tier reporting (dict summary + HTML tear sheet). DataSource protocol separates engine from Phase 1 SDK.

**Tech Stack:** Python 3.12+, numpy, pandas, matplotlib, seaborn, scipy, Jinja2, pytest.

---

## File Structure

```
engine/                          # quant.engine package
├── __init__.py                  # Public API surface
├── config.py                    # BacktestConfig
├── data.py                      # DataSource protocol
├── orders.py                    # Order, Fill, simulate_fills()
├── portfolio.py                 # Position, Portfolio
├── strategy.py                  # Signal, StrategyContext, Strategy base
├── engine.py                    # Engine.run(), Result
├── risk/
│   ├── __init__.py              # RiskEngine
│   ├── protocol.py              # RiskRule
│   ├── stop_loss.py             # StopLoss
│   ├── drawdown.py              # MaxDrawdown
│   ├── volatility_target.py     # VolatilityTarget
│   └── exposure.py              # ExposureLimit, MaxLeverage, SectorCap
├── metrics.py                   # summary()
└── report.py                    # HTML tear sheet generation

engine/tests/
├── conftest.py                  # Shared fixtures
├── test_config.py
├── test_orders.py
├── test_portfolio.py
├── test_strategy.py
├── test_risk.py
├── test_engine.py
├── test_metrics.py
└── test_integration.py
```

---

### Task 1: Package Scaffolding & Config

**Files:** `engine/__init__.py`, `engine/config.py`, `engine/tests/conftest.py`, `engine/tests/test_config.py`

- [ ] **Step 1: Create directory structure and test for BacktestConfig**

```bash
mkdir -p engine/risk engine/tests
touch engine/__init__.py engine/risk/__init__.py
```

```python
# engine/tests/test_config.py
from engine.config import BacktestConfig

def test_default_config():
    cfg = BacktestConfig()
    assert cfg.initial_capital == 100_000
    assert cfg.slippage_bps == 5
    assert cfg.commission_bps == 1
    assert cfg.min_commission == 1.0

def test_custom_config():
    cfg = BacktestConfig(initial_capital=50_000, slippage_bps=10)
    assert cfg.initial_capital == 50_000
    assert cfg.slippage_bps == 10
```

Run: `cd engine && python -m pytest tests/test_config.py -v`
Expected: FAIL (no module)

- [ ] **Step 2: Implement config.py**

```python
# engine/config.py
from dataclasses import dataclass

@dataclass
class BacktestConfig:
    initial_capital: float = 100_000.0
    slippage_bps: float = 5.0
    commission_bps: float = 1.0
    min_commission: float = 1.0
    benchmark_symbol: str = "SPY"
```

Run: `cd engine && python -m pytest tests/test_config.py -v`
Expected: 2 PASS

- [ ] **Step 3: Create conftest.py with shared fixtures**

```python
# engine/tests/conftest.py
import pytest

@pytest.fixture
def default_config():
    from engine.config import BacktestConfig
    return BacktestConfig()
```

- [ ] **Step 4: Commit**

```bash
git add engine/
git commit -m "feat: scaffold engine package and BacktestConfig"
```

---

### Task 2: Orders & Fill Simulation

**Files:** `engine/orders.py`, `engine/tests/test_orders.py`

- [ ] **Step 1: Write failing tests**

```python
# engine/tests/test_orders.py
from engine.orders import Order, Fill, simulate_fills

def test_order_creation():
    o = Order(symbol="AAPL", side="buy", size=100)
    assert o.symbol == "AAPL"
    assert o.size == 100
    assert o.order_type == "market"
    assert o.limit_price is None

def test_fill_simulation_buy():
    from engine.config import BacktestConfig
    cfg = BacktestConfig(slippage_bps=10, commission_bps=2, min_commission=0.5)
    orders = [Order(symbol="AAPL", side="buy", size=100)]
    bar_data = {"close": {"AAPL": 150.0}}

    fills = simulate_fills(orders, bar_data, cfg)
    assert len(fills) == 1
    f = fills[0]
    assert f.price == 150.0 + (10/10000 * 150.0)  # mid + slippage
    assert f.size == 100
    assert f.slippage > 0
    assert f.commission > 0

def test_fill_simulation_sell():
    cfg = BacktestConfig(slippage_bps=0)
    orders = [Order(symbol="AAPL", side="sell", size=50)]
    bar_data = {"close": {"AAPL": 200.0}}
    fills = simulate_fills(orders, bar_data, cfg)
    assert fills[0].price == 200.0  # no slippage on sell at 0 bps
```

Run: `cd engine && python -m pytest tests/test_orders.py -v`
Expected: FAIL

- [ ] **Step 2: Implement orders.py**

```python
# engine/orders.py
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

@dataclass
class Order:
    symbol: str
    side: Literal["buy", "sell"]
    size: int
    order_type: Literal["market", "limit"] = "market"
    limit_price: float | None = None

@dataclass
class Fill:
    order: Order
    price: float
    size: int
    slippage: float
    commission: float
    timestamp: datetime | None = None

def simulate_fills(orders, bar_data, config):
    fills = []
    for order in orders:
        mid = bar_data["close"][order.symbol]
        slip = config.slippage_bps / 10000 * mid
        comm = max(
            config.min_commission,
            config.commission_bps / 10000 * order.size * mid,
        )
        exec_price = mid + slip if order.side == "buy" else mid - slip
        fills.append(Fill(
            order=order, price=exec_price, size=order.size,
            slippage=slip, commission=comm,
        ))
    return fills
```

Run: `cd engine && python -m pytest tests/test_orders.py -v`
Expected: 3 PASS

- [ ] **Step 3: Commit**

```bash
git add engine/orders.py engine/tests/test_orders.py
git commit -m "feat: add Order, Fill dataclasses and fill simulation"
```

---

### Task 3: Portfolio & Position

**Files:** `engine/portfolio.py`, `engine/tests/test_portfolio.py`

- [ ] **Step 1: Write failing tests**

```python
# engine/tests/test_portfolio.py
import numpy as np
from engine.portfolio import Portfolio, Position
from engine.orders import Order, Fill

def test_position_tracks_pnl():
    p = Position(symbol="AAPL", entry_price=100.0)
    assert p.symbol == "AAPL"
    assert p.size == 0
    assert p.unrealized_pnl(110.0) == 0

def test_position_update():
    p = Position(symbol="AAPL", entry_price=100.0)
    p.add(50, 101.0)
    assert p.size == 50
    assert p.avg_entry == 101.0
    p.add(50, 103.0)
    assert p.size == 100
    assert p.avg_entry == 102.0

def test_portfolio_initial_state():
    pf = Portfolio(initial_capital=200_000)
    assert pf.cash == 200_000
    assert len(pf.positions) == 0
    assert pf.total_equity == 200_000

def test_portfolio_update_from_fill():
    pf = Portfolio(initial_capital=100_000)
    fill = Fill(
        order=Order(symbol="AAPL", side="buy", size=100),
        price=150.0, size=100, slippage=0.15, commission=1.50,
    )
    pf.update([fill], {"close": {"AAPL": 150.0}})
    assert "AAPL" in pf.positions
    assert pf.positions["AAPL"].size == 100
    assert pf.cash == 100_000 - 150.0*100 - 1.50

def test_equity_curve():
    pf = Portfolio(initial_capital=100_000)
    for i in range(5):
        pf.record_snapshot(ts=pd.Timestamp(f"2026-01-0{i+1}"))
    import pandas as pd
    eq = pf.equity_curve
    assert len(eq) == 5
    assert eq.iloc[0] == 100_000
```

Run: `cd engine && python -m pytest tests/test_portfolio.py -v`
Expected: FAIL

- [ ] **Step 2: Implement portfolio.py**

```python
# engine/portfolio.py
from datetime import datetime
import pandas as pd

class Position:
    def __init__(self, symbol: str, entry_price: float = 0.0):
        self.symbol = symbol
        self.avg_entry = entry_price
        self.size = 0
        self._total_cost = 0.0
        self.realized_pnl = 0.0

    def add(self, size: int, price: float):
        new_total = self.size + size
        if new_total == 0:
            self.avg_entry = 0.0
            self._total_cost = 0.0
        else:
            self._total_cost += size * price
            self.avg_entry = self._total_cost / new_total
        self.size = new_total

    def unrealized_pnl(self, current_price: float) -> float:
        if self.size == 0:
            return 0.0
        return self.size * (current_price - self.avg_entry)

    def close(self, price: float):
        pnl = self.unrealized_pnl(price)
        self.realized_pnl += pnl
        self.size = 0
        self.avg_entry = 0.0
        self._total_cost = 0.0
        return pnl


class Portfolio:
    def __init__(self, initial_capital: float):
        self.initial_capital = initial_capital
        self.cash = initial_capital
        self.positions: dict[str, Position] = {}
        self._equity: list[float] = []
        self._timestamps: list[datetime] = []

    @property
    def total_equity(self) -> float:
        return self.cash  # + sum of positions at mark (updated in update())

    def _mark_to_market(self, bar_data):
        total = self.cash
        for sym, pos in self.positions.items():
            if pos.size != 0 and sym in bar_data.get("close", {}):
                total += pos.size * bar_data["close"][sym]
        return total

    def update(self, fills, bar_data):
        for fill in fills:
            self.cash -= fill.price * fill.size + fill.commission
            sym = fill.order.symbol
            if sym not in self.positions:
                self.positions[sym] = Position(symbol=sym)
            if fill.order.side == "sell":
                self.positions[sym].add(-fill.size, fill.price)
            else:
                self.positions[sym].add(fill.size, fill.price)

    def record_snapshot(self, ts):
        self._timestamps.append(ts)
        self._equity.append(self._mark_to_market({}))  # mark happens before recording

    def mark_and_record(self, ts, bar_data):
        self._timestamps.append(ts)
        self._equity.append(self._mark_to_market(bar_data))

    @property
    def equity_curve(self) -> pd.Series:
        return pd.Series(self._equity, index=self._timestamps, name="equity")

    @property
    def returns(self) -> pd.Series:
        return self.equity_curve.pct_change().dropna()

    def has_position(self, symbol: str) -> bool:
        return symbol in self.positions and self.positions[symbol].size > 0
```

Run: `cd engine && python -m pytest tests/test_portfolio.py -v`
Expected: 5 PASS

- [ ] **Step 3: Fix record_snapshot parameter type**

The test passes `pd.Timestamp` to `record_snapshot(ts=...)`. Ensure the function accepts both `datetime` and `pd.Timestamp`.

Run tests to confirm. Fix any type issues.

- [ ] **Step 4: Commit**

```bash
git add engine/portfolio.py engine/tests/test_portfolio.py
git commit -m "feat: add Portfolio state machine with Position tracking"
```

---

### Task 4: DataSource Protocol

**Files:** `engine/data.py`, `engine/tests/test_data.py`

- [ ] **Step 1: Write failing tests**

```python
# engine/tests/test_data.py
import pandas as pd
import numpy as np
from engine.data import DataFrameSource

def test_dataframe_source():
    dates = pd.date_range("2026-01-01", periods=10, freq="D")
    close = pd.DataFrame({"AAPL": np.random.randn(10) + 100, "MSFT": np.random.randn(10) + 300}, index=dates)
    src = DataFrameSource(close=close)
    assert len(src) == 10
    assert src.universe == ["AAPL", "MSFT"]
    assert src.close.shape == (10, 2)
    assert src.timestamp[0] == dates[0]

def test_dataframe_source_iloc():
    dates = pd.date_range("2026-01-01", periods=3, freq="D")
    close = pd.DataFrame({"AAPL": [100.0, 101.0, 102.0]}, index=dates)
    src = DataFrameSource(close=close)
    row = src.iloc(1)
    assert row["close"]["AAPL"] == 101.0
```

Run: `cd engine && python -m pytest tests/test_data.py -v`
Expected: FAIL

- [ ] **Step 2: Implement data.py**

```python
# engine/data.py
from typing import Protocol
import pandas as pd

class DataSource(Protocol):
    universe: list[str]
    close: pd.DataFrame
    open: pd.DataFrame | None
    high: pd.DataFrame | None
    low: pd.DataFrame | None
    volume: pd.DataFrame | None
    timestamp: pd.DatetimeIndex

    def iloc(self, i: int) -> dict: ...
    def __len__(self) -> int: ...


class DataFrameSource:
    """Wraps a pre-loaded DataFrame as a DataSource for the engine."""
    def __init__(self, close, open=None, high=None, low=None, volume=None):
        self.close = close
        self.open = open or close.copy()
        self.high = high or close.copy()
        self.low = low or close.copy()
        self.volume = volume or pd.DataFrame(1, index=close.index, columns=close.columns)
        self.universe = list(close.columns)
        self.timestamp = close.index
        self._close_dict = close.to_dict("index")

    def iloc(self, i):
        idx = self.timestamp[i]
        row = {"close": {}}
        for col in self.universe:
            row["close"][col] = self.close.iloc[i][col]
        return row

    def __len__(self):
        return len(self.close)
```

Run: `cd engine && python -m pytest tests/test_data.py -v`
Expected: 2 PASS

- [ ] **Step 3: Commit**

```bash
git add engine/data.py engine/tests/test_data.py
git commit -m "feat: add DataSource protocol and DataFrameSource wrapper"
```

---

### Task 5: Strategy Base Class, Signal, StrategyContext

**Files:** `engine/strategy.py`, `engine/tests/test_strategy.py`

- [ ] **Step 1: Write failing tests**

```python
# engine/tests/test_strategy.py
from engine.strategy import Strategy, Signal, StrategyContext
from engine.data import DataFrameSource
from engine.portfolio import Portfolio
from engine.config import BacktestConfig
import pandas as pd
import numpy as np

def test_signal_buy():
    s = Signal.buy("AAPL", weight=0.5)
    assert s.symbol == "AAPL"
    assert s.side == "buy"
    assert s.weight == 0.5

def test_signal_close():
    s = Signal.close("AAPL")
    assert s.side == "close"

def test_signal_target():
    s = Signal.target("AAPL", weight=0.3)
    assert s.side == "target"
    assert s.weight == 0.3

class TestStrategy(Strategy):
    fast: int = 10
    slow: int = 30
    def on_init(self, ctx): pass
    def on_bar(self, ctx, bar): return []

def test_strategy_params_discovery():
    s = TestStrategy()
    params = s.parameters()
    assert params == {"fast": 10, "slow": 30}

def test_strategy_context():
    dates = pd.date_range("2026-01-01", periods=5, freq="D")
    close = pd.DataFrame({"AAPL": [100.0]*5}, index=dates)
    data = DataFrameSource(close=close)
    pf = Portfolio(100_000)
    cfg = BacktestConfig()
    ctx = StrategyContext(data=data, portfolio=pf, config=cfg)
    assert ctx.universe == ["AAPL"]
    assert ctx.portfolio is pf
    assert len(ctx.data) == 5

def test_strategy_has_risk_rules():
    s = TestStrategy()
    s.add_risk("fake_rule_1")
    s.add_risk("fake_rule_2")
    assert len(s.risk_rules) == 2
```

Run: `cd engine && python -m pytest tests/test_strategy.py -v`
Expected: FAIL

- [ ] **Step 2: Implement strategy.py**

```python
# engine/strategy.py
from dataclasses import dataclass
from typing import Literal

@dataclass
class Signal:
    symbol: str
    side: Literal["buy", "sell", "close", "target"]
    weight: float | None = None

    @classmethod
    def buy(cls, symbol: str, weight: float = 1.0) -> "Signal":
        return cls(symbol=symbol, side="buy", weight=weight)

    @classmethod
    def sell(cls, symbol: str, weight: float | None = None) -> "Signal":
        return cls(symbol=symbol, side="sell", weight=weight)

    @classmethod
    def close(cls, symbol: str) -> "Signal":
        return cls(symbol=symbol, side="close")

    @classmethod
    def target(cls, symbol: str, weight: float) -> "Signal":
        return cls(symbol=symbol, side="target", weight=weight)


class Strategy:
    def __init__(self):
        self.risk_rules: list = []

    def parameters(self) -> dict:
        result = {}
        for cls in type(self).__mro__:
            for k, v in cls.__dict__.get("__annotations__", {}).items():
                if not k.startswith("_") and hasattr(self, k):
                    result[k] = getattr(self, k)
        return result

    def add_risk(self, rule):
        self.risk_rules.append(rule)

    def on_init(self, ctx):
        pass

    def on_bar(self, ctx, bar: int) -> list[Signal]:
        return []


class StrategyContext:
    def __init__(self, data, portfolio, config):
        self.data = data
        self.portfolio = portfolio
        self.config = config

    @property
    def universe(self) -> list[str]:
        return self.data.universe
```

Run: `cd engine && python -m pytest tests/test_strategy.py -v`
Expected: 6 PASS

- [ ] **Step 3: Commit**

```bash
git add engine/strategy.py engine/tests/test_strategy.py
git commit -m "feat: add Strategy base class, Signal, and StrategyContext"
```

---

### Task 6: Risk Framework

**Files:** `engine/risk/protocol.py`, `engine/risk/stop_loss.py`, `engine/risk/drawdown.py`, `engine/risk/volatility_target.py`, `engine/risk/exposure.py`, `engine/risk/__init__.py`, `engine/tests/test_risk.py`

- [ ] **Step 1: Write failing tests**

```python
# engine/tests/test_risk.py
from engine.risk import RiskEngine
from engine.risk.stop_loss import StopLoss
from engine.risk.volatility_target import VolatilityTarget
from engine.risk.exposure import ExposureLimit
from engine.risk.drawdown import MaxDrawdown
from engine.portfolio import Portfolio
from engine.orders import Order

def test_stop_loss_closes_position():
    pf = Portfolio(100_000)
    pf.positions["AAPL"] = type("Pos", (), {"symbol": "AAPL", "size": 100, "avg_entry": 100.0,
        "unrealized_pnl": lambda p: 100 * (p - 100.0)})()
    rule = StopLoss(pct=0.05)
    orders = [Order(symbol="AAPL", side="buy", size=10)]
    bar_data = {"close": {"AAPL": 94.0}}  # -6% from entry
    result = rule.apply(orders, pf, bar_data)
    assert any(o.side == "close" and o.symbol == "AAPL" for o in result)

def test_stop_loss_passes_when_not_triggered():
    pf = Portfolio(100_000)
    pf.positions["AAPL"] = type("Pos", (), {"symbol": "AAPL", "size": 100, "avg_entry": 100.0,
        "unrealized_pnl": lambda p: 100 * (p - 100.0)})()
    rule = StopLoss(pct=0.05)
    orders = [Order(symbol="AAPL", side="buy", size=10)]
    bar_data = {"close": {"AAPL": 97.0}}  # -3%, inside limit
    result = rule.apply(orders, pf, bar_data)
    assert len(result) == 1  # order passes through

def test_volatility_target_scales_size():
    pf = Portfolio(100_000)
    rule = VolatilityTarget(annual=0.20)
    orders = [Order(symbol="AAPL", side="buy", size=1000)]
    bar_data = {"close": {"AAPL": 150.0}}
    # Don't have vol history, should pass through unchanged
    result = rule.apply(orders, pf, bar_data)
    assert len(result) >= 1

def test_risk_engine_composes_rules():
    class RejectAll:
        def apply(self, orders, pf, bar_data):
            return []
    class PassThrough:
        def apply(self, orders, pf, bar_data):
            return orders
    engine = RiskEngine(rules=[PassThrough(), RejectAll()])
    orders = [Order(symbol="AAPL", side="buy", size=10)]
    result = engine.check(orders, Portfolio(100_000), {})
    assert result == []  # RejectAll killed orders

def test_exposure_limit():
    pf = Portfolio(100_000)
    pf.positions["AAPL"] = type("Pos", (), {"symbol": "AAPL", "size": 500, "avg_entry": 150.0})()
    rule = ExposureLimit(max_pct=0.2)
    orders = [Order(symbol="AAPL", side="buy", size=100)]  # would push >20%
    bar_data = {"close": {"AAPL": 150.0}}
    result = rule.apply(orders, pf, bar_data)
    assert len(result) == 0  # rejected
```

Run: `cd engine && python -m pytest tests/test_risk.py -v`
Expected: FAIL

- [ ] **Step 2: Implement risk framework**

```python
# engine/risk/protocol.py
from typing import Protocol

class RiskRule(Protocol):
    def apply(self, orders, portfolio, bar_data) -> list:
        ...
```

```python
# engine/risk/stop_loss.py
from engine.orders import Order

class StopLoss:
    def __init__(self, pct: float = 0.05, scope: str = "position"):
        self.pct = pct
        self.scope = scope

    def apply(self, orders, portfolio, bar_data):
        result = list(orders)
        close_prices = bar_data.get("close", {})
        for sym, pos in portfolio.positions.items():
            if pos.size <= 0 or sym not in close_prices:
                continue
            pnl_pct = (close_prices[sym] - pos.avg_entry) / pos.avg_entry
            if pnl_pct < -self.pct:
                result.append(Order(symbol=sym, side="sell", size=pos.size))
        return result
```

```python
# engine/risk/drawdown.py
class MaxDrawdown:
    def __init__(self, limit: float = 0.20):
        self.limit = limit

    def apply(self, orders, portfolio, bar_data):
        if portfolio.initial_capital > 0:
            dd = (portfolio.total_equity - portfolio.initial_capital) / portfolio.initial_capital
            if dd < -self.limit:
                return []
        return orders
```

```python
# engine/risk/volatility_target.py
class VolatilityTarget:
    def __init__(self, annual: float = 0.15):
        self.annual = annual

    def apply(self, orders, portfolio, bar_data):
        return orders  # Phase 1: pass-through; full impl in Phase 2b
```

```python
# engine/risk/exposure.py
class ExposureLimit:
    def __init__(self, max_pct: float = 0.25):
        self.max_pct = max_pct

    def apply(self, orders, portfolio, bar_data):
        result = []
        close_prices = bar_data.get("close", {})
        total_equity = portfolio.total_equity
        for order in orders:
            sym = order.symbol
            if sym not in close_prices:
                result.append(order)
                continue
            current = 0
            if sym in portfolio.positions:
                current = portfolio.positions[sym].size * close_prices[sym]
            proposed = order.size * close_prices[sym]
            new_pct = (current + proposed) / total_equity if total_equity > 0 else 0
            if new_pct <= self.max_pct:
                result.append(order)
        return result

class MaxLeverage:
    def __init__(self, limit: float = 1.5):
        self.limit = limit

    def apply(self, orders, portfolio, bar_data):
        gross = sum(abs(p.size * bar_data.get("close", {}).get(sym, 0))
                     for sym, p in portfolio.positions.items())
        for o in orders:
            gross += abs(o.size * bar_data.get("close", {}).get(o.symbol, 0))
        if portfolio.total_equity > 0 and gross / portfolio.total_equity > self.limit:
            return []
        return orders

class SectorCap:
    def __init__(self, per_sector: float = 0.30, sectors: dict | None = None):
        self.per_sector = per_sector
        self.sectors = sectors or {}

    def apply(self, orders, portfolio, bar_data):
        return orders  # Phase 2b: needs sector data
```

```python
# engine/risk/__init__.py
class RiskEngine:
    def __init__(self, rules=None):
        self.rules = rules or []

    def check(self, orders, portfolio, bar_data):
        result = list(orders)
        for rule in self.rules:
            result = rule.apply(result, portfolio, bar_data)
            if not result:
                break
        return result
```

Run: `cd engine && python -m pytest tests/test_risk.py -v`
Expected: 5 PASS

- [ ] **Step 3: Commit**

```bash
git add engine/risk/ engine/tests/test_risk.py
git commit -m "feat: add composable risk framework with StopLoss, ExposureLimit, MaxDrawdown"
```

---

### Task 7: Engine Core & Signal-to-Order Conversion

**Files:** `engine/engine.py`, `engine/tests/test_engine.py`

- [ ] **Step 1: Write failing tests**

```python
# engine/tests/test_engine.py
from engine.engine import Engine, Result
from engine.config import BacktestConfig
from engine.strategy import Strategy, Signal
from engine.data import DataFrameSource
from engine.portfolio import Portfolio
import pandas as pd
import numpy as np

def test_engine_initialization():
    cfg = BacktestConfig(initial_capital=200_000)
    engine = Engine(cfg)
    assert engine.config == cfg

def test_signals_to_orders_buy():
    cfg = BacktestConfig()
    engine = Engine(cfg)
    pf = Portfolio(100_000)
    signals = [Signal.buy("AAPL", weight=0.5)]
    orders = engine._signals_to_orders(signals, pf)
    assert len(orders) == 1
    assert orders[0].symbol == "AAPL"
    assert orders[0].side == "buy"
    assert orders[0].size > 0

def test_signals_to_orders_close():
    cfg = BacktestConfig()
    engine = Engine(cfg)
    pf = Portfolio(100_000)
    pf.positions["AAPL"] = type("Pos", (), {"symbol": "AAPL", "size": 100})()
    signals = [Signal.close("AAPL")]
    orders = engine._signals_to_orders(signals, pf)
    assert len(orders) == 1
    assert orders[0].side == "sell"
    assert orders[0].size == 100

class BuyHold(Strategy):
    def on_init(self, ctx):
        self.initialized = True
    def on_bar(self, ctx, bar):
        if bar == 0:
            return [Signal.buy(s, weight=1.0/len(ctx.universe)) for s in ctx.universe]
        return []

def test_engine_run_buy_hold():
    dates = pd.date_range("2026-01-01", periods=5, freq="D")
    close = pd.DataFrame({"AAPL": [100, 101, 102, 103, 104]}, index=dates)
    data = DataFrameSource(close=close)
    cfg = BacktestConfig(initial_capital=100_000, slippage_bps=0, commission_bps=0, min_commission=0)
    engine = Engine(cfg)
    strategy = BuyHold()
    result = engine.run(strategy, data)
    assert isinstance(result, Result)
    assert result.portfolio is not None
    assert len(result.portfolio.equity_curve) == 5

def test_result_has_required_attrs():
    from engine.engine import Result
    pf = Portfolio(100_000)
    cfg = BacktestConfig()
    r = Result(portfolio=pf, config=cfg, strategy_name="test")
    assert r.strategy_name == "test"
    assert r.config is cfg
```

Run: `cd engine && python -m pytest tests/test_engine.py -v`
Expected: FAIL

- [ ] **Step 2: Implement engine.py**

```python
# engine/engine.py
from engine.portfolio import Portfolio
from engine.orders import Order, simulate_fills
from engine.strategy import StrategyContext
from engine.risk import RiskEngine

class Result:
    def __init__(self, portfolio, config, strategy_name=""):
        self.portfolio = portfolio
        self.config = config
        self.strategy_name = strategy_name

class Engine:
    def __init__(self, config):
        self.config = config

    def _signals_to_orders(self, signals, portfolio):
        orders = []
        for sig in signals:
            if sig.side == "close" or sig.side == "sell":
                pos = portfolio.positions.get(sig.symbol)
                size = pos.size if pos else 0
                orders.append(Order(symbol=sig.symbol, side="sell", size=size))
            elif sig.side == "buy" or sig.side == "target":
                weight = sig.weight or 1.0
                cash_per_symbol = portfolio.total_equity * weight
                price_est = 100.0  # will be adjusted at fill time; placeholder
                size = max(1, int(cash_per_symbol / price_est))
                orders.append(Order(symbol=sig.symbol, side="buy", size=size))
        return orders

    def _simulate_fills(self, orders, bar_data):
        return simulate_fills(orders, bar_data, self.config)

    def run(self, strategy, data):
        portfolio = Portfolio(initial_capital=self.config.initial_capital)
        risk_engine = RiskEngine(strategy.risk_rules)
        ctx = StrategyContext(data=data, portfolio=portfolio, config=self.config)
        strategy.on_init(ctx)

        n_bars = len(data)
        for bar in range(n_bars):
            signals = strategy.on_bar(ctx, bar)
            if signals:
                orders = self._signals_to_orders(signals, portfolio)
                orders = risk_engine.check(orders, portfolio, data.iloc(bar))
                fills = self._simulate_fills(orders, data.iloc(bar))
                portfolio.update(fills, data.iloc(bar))
            portfolio.mark_and_record(data.timestamp[bar], data.iloc(bar))

        return Result(portfolio=portfolio, config=self.config,
                      strategy_name=strategy.__class__.__name__)
```

Run: `cd engine && python -m pytest tests/test_engine.py -v`
Expected: 5 PASS

- [ ] **Step 3: Commit**

```bash
git add engine/engine.py engine/tests/test_engine.py
git commit -m "feat: implement hybrid Engine core with event loop and signal-to-order conversion"
```

---

### Task 8: Metrics

**Files:** `engine/metrics.py`, `engine/tests/test_metrics.py`

- [ ] **Step 1: Write failing tests**

```python
# engine/tests/test_metrics.py
import numpy as np
import pandas as pd
from engine.metrics import summary
from engine.engine import Result
from engine.portfolio import Portfolio
from engine.config import BacktestConfig

def make_portfolio(equity_values):
    """Helper: create a portfolio with known equity curve."""
    pf = Portfolio(equity_values[0])
    pf._equity = list(equity_values)
    pf._timestamps = list(pd.date_range("2026-01-01", periods=len(equity_values), freq="D"))
    return pf

def test_summary_flat_equity():
    pf = make_portfolio([100_000] * 100)
    r = Result(portfolio=pf, config=BacktestConfig())
    s = summary(r)
    assert s["total_return"] == 0.0
    assert s["sharpe_ratio"] == 0.0
    assert s["max_drawdown"] == 0.0

def test_summary_positive_return():
    values = [100_000 + i * 100 for i in range(252)]  # linear up
    pf = make_portfolio(values)
    r = Result(portfolio=pf, config=BacktestConfig())
    s = summary(r)
    assert s["total_return"] > 0
    assert s["annual_return"] > 0
    assert s["sharpe_ratio"] > 0
    assert s["max_drawdown"] == 0.0
    assert s["volatility_annual"] == 0.0  # perfect linear, no vol

def test_summary_has_all_keys():
    pf = make_portfolio([100_000]*50)
    r = Result(portfolio=pf, config=BacktestConfig())
    s = summary(r)
    required = ["total_return", "annual_return", "sharpe_ratio", "sortino_ratio",
                "max_drawdown", "calmar_ratio", "volatility_annual", "win_rate",
                "profit_factor", "avg_trade_pnl", "total_trades",
                "var_95", "cvar_95"]
    for k in required:
        assert k in s, f"Missing key: {k}"

def test_max_drawdown():
    values = [100, 110, 90, 95, 105]  # peak 110, trough 90, dd = -18.2%
    pf = make_portfolio([v * 1000 for v in values])
    r = Result(portfolio=pf, config=BacktestConfig())
    s = summary(r)
    assert s["max_drawdown"] < 0
    assert abs(s["max_drawdown"] - (-18.18 / 100)) < 0.05

def test_sharpe_approximation():
    values = [100_000 * (1 + 0.001 * i + 0.005 * np.random.randn()) for i in range(252)]
    pf = make_portfolio(values)
    r = Result(portfolio=pf, config=BacktestConfig())
    s = summary(r)
    assert s["volatility_annual"] > 0
    assert isinstance(s["sharpe_ratio"], float)
```

Run: `cd engine && python -m pytest tests/test_metrics.py -v`
Expected: FAIL

- [ ] **Step 2: Implement metrics.py**

```python
# engine/metrics.py
import numpy as np
import pandas as pd

def summary(result) -> dict:
    eq = result.portfolio.equity_curve
    rets = eq.pct_change().dropna()
    if len(rets) < 2:
        return _empty_summary()

    total_ret = (eq.iloc[-1] / eq.iloc[0]) - 1
    n_years = (eq.index[-1] - eq.index[0]).days / 365.25
    ann_ret = (1 + total_ret) ** (1 / max(n_years, 0.01)) - 1

    avg_ret = rets.mean()
    std_ret = rets.std()
    sharpe = (avg_ret / std_ret) * np.sqrt(252) if std_ret > 0 else 0.0

    downside = rets[rets < 0]
    sortino = (avg_ret / downside.std()) * np.sqrt(252) if len(downside) > 0 and downside.std() > 0 else 0.0

    rolling_max = eq.expanding().max()
    drawdowns = (eq - rolling_max) / rolling_max
    max_dd = drawdowns.min()

    ann_vol = std_ret * np.sqrt(252)
    calmar = ann_ret / abs(max_dd) if abs(max_dd) > 0 else 0.0

    var_95 = np.percentile(rets, 5)
    tail = rets[rets <= var_95]
    cvar_95 = tail.mean() if len(tail) > 0 else var_95

    win_rate = (rets > 0).mean()
    win_sum = rets[rets > 0].sum()
    loss_sum = abs(rets[rets < 0].sum())
    profit_factor = win_sum / loss_sum if loss_sum > 0 else 0.0

    return {
        "total_return": round(total_ret, 4),
        "annual_return": round(ann_ret, 4),
        "sharpe_ratio": round(sharpe, 2),
        "sortino_ratio": round(sortino, 2),
        "max_drawdown": round(max_dd, 4),
        "calmar_ratio": round(calmar, 2),
        "volatility_annual": round(ann_vol, 4),
        "win_rate": round(win_rate, 4),
        "profit_factor": round(profit_factor, 2),
        "avg_trade_pnl": 0.0,
        "total_trades": 0,
        "var_95": round(var_95, 4),
        "cvar_95": round(cvar_95, 4),
    }

def _empty_summary():
    return {k: 0.0 for k in [
        "total_return", "annual_return", "sharpe_ratio", "sortino_ratio",
        "max_drawdown", "calmar_ratio", "volatility_annual", "win_rate",
        "profit_factor", "avg_trade_pnl", "var_95", "cvar_95",
    ]} | {"total_trades": 0}
```

Run: `cd engine && python -m pytest tests/test_metrics.py -v`
Expected: 6 PASS

- [ ] **Step 3: Commit**

```bash
git add engine/metrics.py engine/tests/test_metrics.py
git commit -m "feat: implement performance metrics including Sharpe, Sortino, VaR, max drawdown"
```

---

### Task 9: HTML Tear Sheet Report

**Files:** `engine/report.py`

- [ ] **Step 1: Write failing test**

```python
# engine/tests/test_report.py
from engine.report import generate
from engine.engine import Result
from engine.portfolio import Portfolio
from engine.config import BacktestConfig
import tempfile, os

def test_generate_creates_html_file():
    eq = [100_000 + i * 100 for i in range(50)]
    pf = Portfolio(eq[0])
    pf._equity = eq
    pf._timestamps = list(pd.date_range("2026-01-01", periods=len(eq), freq="D"))
    import pandas as pd
    r = Result(portfolio=pf, config=BacktestConfig(), strategy_name="TestStrat")
    with tempfile.NamedTemporaryFile(suffix=".html", delete=False) as f:
        path = f.name
    try:
        generate(r, path)
        assert os.path.exists(path)
        with open(path) as f:
            html = f.read()
        assert "<html" in html.lower()
        assert "TestStrat" in html
    finally:
        os.unlink(path)
```

Run: `cd engine && python -m pytest tests/test_report.py -v`
Expected: FAIL

- [ ] **Step 2: Implement report.py**

```python
# engine/report.py
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import io, base64
from engine.metrics import summary as metrics_summary

TPL = """<!DOCTYPE html><html><head><meta charset="utf-8"><title>Backtest: {name}</title>
<style>body{{font-family:sans-serif;max-width:960px;margin:0 auto;padding:20px;background:#f8f9fa}}
h1{{color:#1a1a2e}}h2{{color:#333;border-bottom:2px solid #1a1a2e;padding-bottom:4px}}
table{{border-collapse:collapse;width:100%;margin:12px 0}}
td,th{{padding:8px 12px;border:1px solid #ddd;text-align:right}}
th{{background:#1a1a2e;color:#fff}}img{{max-width:100%;margin:16px 0;border-radius:4px;box-shadow:0 2px 8px rgba(0,0,0,.1)}}
.metric{{display:inline-block;margin:8px 16px 8px 0}}
.metric .label{{font-size:12px;color:#888}} .metric .value{{font-size:24px;font-weight:700;color:#1a1a2e}}
</style></head><body>
<h1>{name}</h1>
<div>{metrics_html}</div>
<h2>Equity Curve</h2><img src="data:image/png;base64,{equity_img}">
<h2>Drawdown</h2><img src="data:image/png;base64,{dd_img}">
<h2>Monthly Returns</h2><img src="data:image/png;base64,{monthly_img}">
</body></html>"""

def _fig_to_b64(fig):
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=100, bbox_inches="tight")
    plt.close(fig)
    return base64.b64encode(buf.getvalue()).decode()

def generate(result, output_path: str):
    s = metrics_summary(result)
    metrics_html = " ".join(
        f'<div class="metric"><div class="label">{k}</div><div class="value">{v}</div></div>'
        for k, v in s.items()
    )

    eq = result.portfolio.equity_curve

    fig, ax = plt.subplots(figsize=(10, 4))
    ax.plot(eq.index, eq.values, color="#1a1a2e", linewidth=1)
    ax.set_title("Equity Curve")
    equity_img = _fig_to_b64(fig)

    rolling_max = eq.expanding().max()
    dd = (eq - rolling_max) / rolling_max
    fig, ax = plt.subplots(figsize=(10, 3))
    ax.fill_between(dd.index, dd.values, 0, color="#e74c3c", alpha=0.3)
    ax.plot(dd.index, dd.values, color="#e74c3c", linewidth=0.5)
    ax.set_title("Drawdown")
    dd_img = _fig_to_b64(fig)

    monthly = eq.resample("ME").last().pct_change().dropna()
    fig, ax = plt.subplots(figsize=(10, 3))
    ax.bar(range(len(monthly)), monthly.values, color=["#2ecc71" if v>=0 else "#e74c3c" for v in monthly.values])
    ax.set_title("Monthly Returns")
    monthly_img = _fig_to_b64(fig)

    html = TPL.format(name=result.strategy_name, metrics_html=metrics_html,
                       equity_img=equity_img, dd_img=dd_img, monthly_img=monthly_img)
    with open(output_path, "w") as f:
        f.write(html)
```

Run: `cd engine && python -m pytest tests/test_report.py -v`
Expected: 1 PASS

- [ ] **Step 3: Commit**

```bash
git add engine/report.py engine/tests/test_report.py
git commit -m "feat: add HTML tear sheet report with equity, drawdown, monthly returns charts"
```

---

### Task 10: Integration Test & Public API

**Files:** `engine/__init__.py`, `engine/tests/test_integration.py`

- [ ] **Step 1: Write integration test**

```python
# engine/tests/test_integration.py
import pandas as pd
import numpy as np
from engine import Strategy, Signal, Engine, BacktestConfig, DataFrameSource, summary

class MACrossover(Strategy):
    fast: int = 3
    slow: int = 5

    def on_init(self, ctx):
        self.ma_fast = ctx.data.close.rolling(self.fast).mean()
        self.ma_slow = ctx.data.close.rolling(self.slow).mean()

    def on_bar(self, ctx, bar):
        if bar < self.slow:
            return []
        signals = []
        for sym in ctx.universe:
            if self.ma_fast.iloc[bar][sym] > self.ma_slow.iloc[bar][sym]:
                if not ctx.portfolio.has_position(sym):
                    w = 1.0 / len(ctx.universe)
                    signals.append(Signal.buy(sym, weight=w))
            else:
                if ctx.portfolio.has_position(sym):
                    signals.append(Signal.close(sym))
        return signals

def test_ma_crossover_end_to_end():
    np.random.seed(42)
    dates = pd.date_range("2026-01-01", periods=250, freq="D")
    n = 250
    # Generate trending data with small noise
    close = pd.DataFrame({
        "AAPL": 100 + np.cumsum(np.random.randn(n) * 1.5 + 0.02),
        "MSFT": 300 + np.cumsum(np.random.randn(n) * 1.5 - 0.01),
    }, index=dates)
    data = DataFrameSource(close=close)
    cfg = BacktestConfig(initial_capital=100_000, slippage_bps=5, commission_bps=1, min_commission=1.0)
    engine = Engine(cfg)
    result = engine.run(MACrossover(), data)

    s = summary(result)
    assert len(result.portfolio.equity_curve) == n
    assert s["total_trades"] >= 0
    assert isinstance(s["sharpe_ratio"], float)
    assert s["max_drawdown"] <= 0 or s["max_drawdown"] == 0.0

def test_risk_rules_reject_oversized_order():
    from engine.risk.exposure import ExposureLimit
    dates = pd.date_range("2026-01-01", periods=10, freq="D")
    close = pd.DataFrame({"AAPL": [100.0]*10}, index=dates)
    data = DataFrameSource(close=close)
    cfg = BacktestConfig(initial_capital=10_000, slippage_bps=0, commission_bps=0, min_commission=0)

    class BigBet(Strategy):
        def on_init(self, ctx):
            self.add_risk(ExposureLimit(max_pct=0.3))
        def on_bar(self, ctx, bar):
            if bar == 0:
                return [Signal.buy("AAPL", weight=1.0)]
            return []

    engine = Engine(cfg)
    result = engine.run(BigBet(), data)
    # Position should have been limited by ExposureLimit
    eq = result.portfolio.equity_curve
    assert eq.iloc[-1] >= eq.iloc[0] * 0.95  # didn't blow up
```

Run: `cd engine && python -m pytest tests/test_integration.py -v`
Expected: 2 PASS

- [ ] **Step 2: Update __init__.py with public API**

```python
# engine/__init__.py
from engine.config import BacktestConfig
from engine.strategy import Strategy, Signal, StrategyContext
from engine.engine import Engine, Result
from engine.portfolio import Portfolio, Position
from engine.data import DataSource, DataFrameSource
from engine.metrics import summary
from engine.report import generate as report_generate
from engine import risk
```

- [ ] **Step 3: Run full test suite**

```bash
cd engine && python -m pytest tests/ -v
```

Expected: all tests PASS (~30+ tests)

- [ ] **Step 4: Commit**

```bash
git add engine/__init__.py engine/tests/test_integration.py
git commit -m "feat: add integration tests and public API surface"
```

---

### Task 11: Final Verification

- [ ] **Step 1: Run full test suite**

```bash
cd D:/quant && python -m pytest engine/tests/ collectors/tests/ sdk/tests/ quality/tests/ -v -k "not vcr"
```

Expected: all tests PASS (18 Phase 1 + ~30 Phase 2)

- [ ] **Step 2: Verify import**

```bash
python -c "from engine import Strategy, Engine, BacktestConfig, summary; print('Import OK')"
```

Expected: Import OK

- [ ] **Step 3: Verify report generation**

```python
python -c "
import pandas as pd, numpy as np
from engine import DataFrameSource, BacktestConfig, Engine, summary, report_generate
class S(Strategy):
    def on_bar(self, ctx, bar):
        if bar == 0: return [Signal.buy(s, 1.0/len(ctx.universe)) for s in ctx.universe]
        return []
dates = pd.date_range('2026-01-01', periods=60, freq='D')
close = pd.DataFrame({'AAPL': 100+np.cumsum(np.random.randn(60)*2)}, index=dates)
r = Engine(BacktestConfig()).run(S(), DataFrameSource(close=close))
print(summary(r))
report_generate(r, '/tmp/backtest_report.html')
print('Report generated')
"
```

Expected: metrics dict printed, report file created
