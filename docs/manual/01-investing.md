# Investment Guide — Running Trading Strategies

How to take strategies from research into live (or paper) trading using the OMS.

## Architecture

```
Strategy (your rules) → Signal → execution algo (TWAP/VWAP) → OrderManager → Broker → Fill
                                                                      ↑
                                                              RiskGateway (pre-trade checks)
```

- **Strategy**: Python class that generates buy/sell signals (same one you wrote in research).
- **Execution algo**: Slices large orders (TWAP=time-weighted, VWAP=volume-weighted).
- **OMS** (Order Management System): Tracks orders through their lifecycle.
- **Broker**: Connects to Alpaca API (paper or live). PaperBroker for local testing.
- **RiskGateway**: Blocks orders that violate position/leverage/drawdown limits.

## Quickstart: Paper Trading a MACrossover

```python
import asyncio
from engine.strategy import Strategy, Signal
from oms.broker import PaperBroker
from oms.manager import OrderManager
from oms.position import PositionTracker
from oms.alerting import AlertManager, ConsoleHandler
from oms.risk_gateway import RiskGateway
from oms.risk_monitor import RiskMonitor
from oms.bridge import forward_signal, convert_signal
from engine.risk.exposure import ExposureLimit
from engine.data import DataFrameSource
from engine.engine import Engine
from engine.config import BacktestConfig
import pandas as pd
import numpy as np

# ---------- 1. Define your strategy ----------
class MACrossover(Strategy):
    fast: int = 20
    slow: int = 50

    def on_init(self, ctx):
        self.ma_fast = ctx.data.close.rolling(self.fast).mean()
        self.ma_slow = ctx.data.close.rolling(self.slow).mean()

    def on_bar(self, ctx, bar):
        if bar < self.slow:
            return []
        for sym in ctx.universe:
            if self.ma_fast.iloc[bar][sym] > self.ma_slow.iloc[bar][sym]:
                if not ctx.portfolio.has_position(sym):
                    return [Signal.target(sym, weight=1.0)]
            else:
                if ctx.portfolio.has_position(sym):
                    return [Signal.close(sym)]
        return []

# ---------- 2. Set up the broker and OMS ----------
broker = PaperBroker(initial_capital=100_000)
order_manager = OrderManager(broker)
position_tracker = PositionTracker(broker)
alerts = AlertManager()
alerts.on_alert(ConsoleHandler())

# ---------- 3. Set up risk controls ----------
gateway = RiskGateway([
    ExposureLimit(max_pct=0.25),  # max 25% in one position
], broker, alerts)

monitor = RiskMonitor(broker, alerts, config={
    "max_drawdown": 0.20,
    "max_leverage": 1.5,
    "max_concentration": 0.30,
})
# In production: asyncio.run(monitor.start(interval_seconds=30))

# ---------- 4. Run the strategy, forwarding signals to OMS ----------
data = DataFrameSource(close=pd.DataFrame({
    "SPY": 450 + np.cumsum(np.random.randn(500) * 1.5),
}, index=pd.date_range("2026-01-01", periods=500, freq="1min")))

cfg = BacktestConfig(initial_capital=100_000)
engine = Engine(cfg)
strategy = MACrossover()

for bar in range(len(data)):
    ctx = engine._build_context(strategy, data)  # simplified
    signals = strategy.on_bar(ctx, bar)
    for sig in signals:
        signal_dict = convert_signal(sig, ctx.portfolio)
        results = forward_signal(signal_dict, broker, order_manager,
                                 position_tracker=position_tracker)
        print(f"Order: {results[0].symbol} {results[0].side} "
              f"qty={results[0].filled_qty} state={results[0].state}")

# ---------- 5. Check results ----------
print("\nPositions:", position_tracker.positions)
print("Alerts:", alerts.count_by_level("warning"), "warnings",
      alerts.count_by_level("critical"), "critical")

# Reconcile with broker
broker_positions = asyncio.run(broker.get_positions())
for bp in broker_positions:
    print(f"  {bp.symbol}: {bp.qty} @ ${bp.avg_entry_price:.2f} "
          f"PnL=${bp.unrealized_pnl:.2f}")
```

## Trading with Alpaca (Paper → Live)

### Credentials

Set environment variables (never commit to git):

```bash
export ALPACA_API_KEY="PK..."
export ALPACA_API_SECRET="..."
```

### Paper trading

```python
from oms.broker.alpaca_broker import AlpacaBroker

broker = AlpacaBroker(paper=True)  # Uses Alpaca paper trading endpoint
# All API calls are identical — same submit_order(), get_positions(), etc.
```

### Going live

```python
broker = AlpacaBroker(paper=False)  # Uses live trading endpoint
# WARNING: Real money. Test thoroughly on paper first.
```

**Live trading checklist:**
1. Run the strategy on paper for at least 1 week with real market data
2. Verify reconcile() returns zero discrepancies
3. Review all alerts — adjust risk limits if too noisy
4. Start with small position sizes (1-10 shares)
5. Monitor the dashboard during first live session

## Execution Algorithms

### TWAP (Time-Weighted Average Price)

Best for: low-urgency orders, avoiding market impact. Splices evenly over time.

```python
from execution.twap import TWAPExecutor

twap = TWAPExecutor(window_seconds=600, slices=10, randomize=True)
# Breaks a 1000-share order into 10×100-share slices over 10 minutes
results = forward_signal(
    {"symbol": "SPY", "side": "buy", "qty": 1000},
    broker, order_manager, execution_algo=twap
)
```

### VWAP (Volume-Weighted Average Price)

Best for: matching or beating the day's VWAP benchmark. Uses historical volume profile.

```python
from execution.vwap import VWAPExecutor
import numpy as np

# Typical intraday volume profile (higher at open/close)
profile = np.array([0.15, 0.10, 0.08, 0.07, 0.08, 0.10, 0.12, 0.15, 0.10, 0.05])
vwap = VWAPExecutor(window_seconds=600, slices=10, volume_profile=profile)
```

### Direct (no algo)

For small orders or urgent fills:

```python
forward_signal({"symbol": "SPY", "side": "buy", "qty": 10}, broker, order_manager)
# Submits a single market order immediately
```

## Risk Controls

### Pre-trade (blocks orders before submission)

```python
from engine.risk.exposure import ExposureLimit
from engine.risk.stop_loss import StopLoss

gateway = RiskGateway([
    ExposureLimit(max_pct=0.25),   # No position > 25% of portfolio
    StopLoss(pct=0.05),            # Close positions down 5%
], broker, alerts)
```

### Post-trade (monitors after fills, fires alerts)

```python
monitor = RiskMonitor(broker, alerts, config={
    "max_drawdown": 0.20,         # Alert if portfolio down 20% from peak
    "max_leverage": 1.5,          # Alert if gross exposure > 1.5× equity
    "max_concentration": 0.30,    # Alert if any position > 30%
    "min_cash_ratio": 0.05,       # Alert if cash < 5% of equity
})
asyncio.run(monitor.start(interval_seconds=30))
```

## Dashboard

Start the monitoring server:

```bash
pip install fastapi uvicorn
uvicorn dashboard.api:app --port 8090
```

Then open `dashboard/index.html` in a browser. The dashboard polls every 3 seconds showing:
- Portfolio equity, cash, buying power
- Active positions with unrealized PnL
- Open orders with fill status
- Risk gauges (drawdown, leverage, concentration)
- Alert feed with severity badges

**To connect the dashboard to live trading,** call `configure()` in your trading script:

```python
from dashboard.api import configure
configure(broker=broker, order_manager=order_manager,
          position_tracker=position_tracker, alert_manager=alerts)
```

## Order Lifecycle

Orders flow through these states. You can track them in the dashboard or programmatically:

```
PENDING → SUBMITTED → ACKNOWLEDGED → PARTIAL_FILL → FILLED
    │           │              │
    └───────────┴──────────────┴──→ CANCELLED
    │
    └──→ REJECTED  (blocked by RiskGateway or broker)
```

Check order status:

```python
order = order_manager.orders[internal_id]
print(order.state)       # "FILLED"
print(order.avg_fill_price)
print(order.filled_qty)
```

## Position Reconciliation

Always reconcile after a trading session to catch discrepancies:

```python
from oms.bridge import reconcile

issues = reconcile(engine_portfolio, position_tracker)
if issues:
    for issue in issues:
        print(f"MISMATCH: {issue}")
else:
    print("All positions reconciled ✓")
```
