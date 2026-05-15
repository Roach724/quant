# Phase 2: Research & Backtesting Engine — Design Spec

**Date:** 2026-05-16
**Status:** Approved
**Scope:** Phase 2 of multi-phase quant trading system

## Overview

Build a custom institutional-grade backtesting and research engine as a Python package (`quant.engine`). The engine runs in Jupyter notebooks (Vertex AI Workbench) for strategy development and scales to Compute Engine for heavy optimization workloads.

**Tech stack:** Python 3.12+, numpy, pandas, matplotlib, seaborn, scipy, Jinja2. No third-party backtesting frameworks.

**Design principle:** Custom from scratch — full control over architecture, no dependency risk, optimal fit to our multi-asset hybrid execution model. All engine state is explicit; pure functions where possible.

## Architecture

```
Notebook / Script
  strategy.run(data) → report.summary()
        │
┌───────┴──────────────────────────────────┐
│  Strategy API          Report / Metrics  │
│  (Strategy base class) (summary, report) │
├──────────────────────────────────────────┤
│  Hybrid Engine Core                      │
│  Engine.run() ← EventLoop + Portfolio    │
├──────────┬──────────┬────────────────────┤
│  Risk    │ Orders   │  DataSource        │
│  Engine  │ & Fills  │  (protocol)        │
├──────────┴──────────┴────────────────────┤
│  quant.data SDK (Phase 1)   BigQuery     │
└──────────────────────────────────────────┘
```

**Key architectural decisions:**
- **Separation of concerns** — Strategy generates signals. Engine executes them. Portfolio tracks positions. Risk constrains orders. Each layer independently testable.
- **Hybrid core** — Vectorized math handles the 90% case (portfolio returns, weight matrices) in numpy. The event loop handles path-dependent logic (stop-loss, dynamic rebalancing). Strategy can choose pure-vectorized, pure-event, or hybrid.
- **All state explicit** — No global state, no hidden side effects. DataSource provides market data. Portfolio provides positions/PnL. StrategyContext wires them together.
- **Custom from scratch** — No `backtrader`, `vectorbt`, `zipline`. We own every line. Core engine ~2000 lines + tests.

## Package Structure

```
engine/                          # quant.engine package
├── strategy.py                  # Strategy base class, Signal, StrategyContext
├── engine.py                    # Engine.run(), event loop orchestration
├── portfolio.py                 # Portfolio state, Position, equity curve
├── orders.py                    # Order, Fill dataclasses, fill simulation
├── risk/
│   ├── __init__.py              # RiskEngine, composable rule chain
│   ├── stop_loss.py             # Per-position and portfolio-level stops
│   ├── volatility_target.py     # Position sizing by vol target
│   ├── drawdown.py              # Max drawdown circuit breaker
│   ├── exposure.py              # Position, sector, leverage limits
│   └── protocol.py             # RiskRule Protocol
├── data.py                      # DataSource protocol, bar data abstraction
├── metrics.py                   # summary(), all performance metrics
├── report.py                    # HTML tear sheet generation (Jinja2 + matplotlib)
└── config.py                    # BacktestConfig (slippage, commission, capital)
```

## Strategy API

```python
from quant.engine import Strategy, Signal, StrategyContext

class MyStrategy(Strategy):
    """User subclass — the only file a researcher writes."""

    # Parameters declared as class attributes (auto-registered for optimization)
    fast_window: int = 20
    slow_window: int = 50

    def on_init(self, ctx: StrategyContext):
        """Called once. Pre-compute indicators, register risk rules."""
        self.ma_fast = ctx.data.close.rolling(self.fast_window).mean()
        self.ma_slow = ctx.data.close.rolling(self.slow_window).mean()
        self.add_risk(StopLoss(pct=0.05))
        self.add_risk(VolatilityTarget(annual=0.15))

    def on_bar(self, ctx: StrategyContext, bar: int) -> list[Signal]:
        """Called every bar. Return signals for the engine to process."""
        cross_up = self.ma_fast.iloc[bar] > self.ma_slow.iloc[bar]
        cross_dn = self.ma_fast.iloc[bar] < self.ma_slow.iloc[bar]

        signals = []
        for symbol in ctx.universe:
            if cross_up[symbol] and not ctx.portfolio.has_position(symbol):
                weight = 1.0 / len(ctx.universe)
                signals.append(Signal.buy(symbol, weight=weight))
            elif cross_dn[symbol]:
                signals.append(Signal.close(symbol))
        return signals
```

**Key design points:**
- `on_bar` receives `bar` as integer index (not datetime) — enables vectorized access via `.iloc[bar]`
- `StrategyContext` gives access to `data`, `portfolio`, `config`, `universe`
- `Signal` is a lightweight dataclass: `buy(symbol, weight)`, `sell(symbol)`, `close(symbol)`, `target(symbol, weight)`
- `add_risk()` registers composable risk rules — evaluated in registration order
- Parameters are class attributes — auto-discoverable for grid search / optimization

## Engine Core (Hybrid Event Loop)

```
Engine.run(strategy, data, config) → Result
  │
  ├─ 1. strategy.on_init(ctx)
  │
  ├─ 2. for each bar:
  │     ├─ signals = strategy.on_bar(ctx, bar)
  │     ├─ if signals:
  │     │   ├─ orders = signals_to_orders(signals, portfolio)
  │     │   ├─ orders = risk.check(orders, portfolio, bar_data)
  │     │   ├─ fills = simulate_fills(orders, bar_data, config)
  │     │   └─ portfolio.update(fills, bar_data)
  │     └─ portfolio.record_snapshot(data.timestamp[bar])
  │
  └─ 3. return Result(portfolio, config)
```

**Hybrid design in practice:**
- The 90% vectorized path (`portfolio.update()` and `portfolio.record_snapshot()`) operates on numpy arrays — weight matrices, returns, equity curves pre-allocated at init.
- The 10% path-dependent path (signals → orders → risk → fills) runs only when the strategy returns non-empty signals. Most bars return nothing.
- Portfolio state is pre-allocated numpy arrays (no pandas `.append()` overhead). Snapshot appends to pre-sized buffers.

```python
class Engine:
    def __init__(self, config: BacktestConfig):
        self.config = config

    def run(self, strategy: Strategy, data: DataSource) -> Result:
        portfolio = Portfolio(initial_capital=self.config.initial_capital)
        risk = RiskEngine(strategy.risk_rules)
        ctx = StrategyContext(data=data, portfolio=portfolio, config=self.config)
        strategy.on_init(ctx)

        n_bars = len(data)
        for bar in range(n_bars):
            signals = strategy.on_bar(ctx, bar)
            if signals:
                orders = self._signals_to_orders(signals, portfolio)
                orders = risk.check(orders, portfolio, data.iloc[bar])
                fills = self._simulate_fills(orders, data.iloc[bar])
                portfolio.update(fills, data.iloc[bar])
            portfolio.record_snapshot(data.timestamp[bar])

        return Result(portfolio=portfolio, config=self.config, strategy_name=strategy.name)
```

## Portfolio (Vectorized State Machine)

```python
class Portfolio:
    def __init__(self, initial_capital: float):
        self.initial_capital = initial_capital
        self.cash = initial_capital
        self.positions: dict[str, Position] = {}
        # Pre-allocated arrays (filled incrementally)
        self._equity: list[float] = []
        self._timestamps: list[datetime] = []

    def update(self, fills: list[Fill], bar_data):
        for fill in fills:
            self.cash -= fill.price * fill.size + fill.commission
            # Update or create Position ...

    def record_snapshot(self, ts: datetime):
        self._timestamps.append(ts)
        self._equity.append(self.total_equity)

    @property
    def equity_curve(self) -> pd.Series:
        return pd.Series(self._equity, index=self._timestamps)

    @property
    def returns(self) -> pd.Series:
        return self.equity_curve.pct_change().dropna()
```

**Position tracks:** symbol, entry price, current size, unrealized PnL, realized PnL, entry time.

## Risk Framework

Composable middleware — each rule is a function that receives orders and can modify or reject them:

```python
class RiskRule(Protocol):
    def apply(self, orders: list[Order], portfolio: Portfolio, bar_data) -> list[Order]: ...

class RiskEngine:
    def __init__(self, rules: list[RiskRule]):
        self.rules = rules

    def check(self, orders, portfolio, bar_data) -> list[Order]:
        result = orders
        for rule in self.rules:
            result = rule.apply(result, portfolio, bar_data)
            if not result:
                break  # All orders rejected — stop the chain
        return result
```

**Built-in rules:**

| Rule | Behavior |
|------|----------|
| `StopLoss(pct=0.05)` | Close position if unrealized loss > 5% from entry |
| `StopLoss(pct=0.20, scope="portfolio")` | Liquidate all if portfolio drawdown > 20% |
| `VolatilityTarget(annual=0.15)` | Scale position size to target 15% annual vol |
| `MaxDrawdown(limit=0.20)` | Circuit breaker — reject new orders if drawdown > limit |
| `ExposureLimit(max_pct=0.25)` | Cap single-position weight at 25% of portfolio |
| `MaxLeverage(limit=1.5)` | Reject orders exceeding leverage limit |
| `SectorCap(per_sector=0.30)` | Limit aggregate exposure per sector to 30% |

**Risk rules are evaluated in registration order** — stop-loss first, then drawdown, then sizing constraints, then exposure limits. Custom rules implement the `RiskRule` protocol.

## Order & Fill Simulation

```python
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
    timestamp: datetime

def simulate_fills(orders, bar_data, config) -> list[Fill]:
    fills = []
    for order in orders:
        mid = bar_data["close"][order.symbol]
        slip = config.slippage_bps / 10000 * mid
        comm = max(config.min_commission,
                   config.commission_bps / 10000 * order.size * mid)
        exec_price = mid + slip if order.side == "buy" else mid - slip
        fills.append(Fill(order=order, price=exec_price, size=order.size,
                          slippage=slip, commission=comm))
    return fills
```

`BacktestConfig` holds: `initial_capital`, `slippage_bps`, `commission_bps`, `min_commission`, `benchmark_symbol`.

## Reporting & Analytics

**Tier 1 — `metrics.summary(result)` → dict**

Returns key metrics as a plain dict for inline notebook display or optimization loops (no rendering overhead):

- `total_return`, `annual_return`, `sharpe_ratio`, `sortino_ratio`
- `max_drawdown`, `calmar_ratio`, `volatility_annual`
- `win_rate`, `profit_factor`, `avg_trade_pnl`, `total_trades`
- `var_95` (95% daily Value at Risk), `cvar_95` (Conditional VaR)
- `beta_vs_benchmark`, `alpha_annual`, `information_ratio`
- `turnover_annual`, `max_leverage`

**Tier 2 — `report.generate(result, output_path)` → HTML**

Full tear sheet with embedded plots:
- Equity curve (cumulative) with benchmark overlay
- Drawdown chart (underwater plot)
- Monthly returns heatmap with annual summary row
- Rolling Sharpe (12-month window) chart
- Top 10 best/worst trades table
- Sector exposure over time (stacked area)
- Return distribution histogram with normal fit overlay

**Plotting:** matplotlib + seaborn for figures, Jinja2 for HTML templating. Static plots suitable for notebooks or saving to files.

## DataSource Protocol

```python
class DataSource(Protocol):
    """How the engine gets market data. Clean separation from Phase 1 SDK."""
    universe: list[str]
    close: pd.DataFrame          # T × N, indexed by timestamp, columns=symbols
    open: pd.DataFrame
    high: pd.DataFrame
    low: pd.DataFrame
    volume: pd.DataFrame
    timestamp: pd.DatetimeIndex   # Shared time axis

    def iloc(self, i: int): ...   # Returns a dict-like row at index i
    def __len__(self) -> int: ... # Number of bars
```

A built-in `QuantSDKDataSource` wraps our Phase 1 `quant.data.bars()` call. Users can implement custom sources (CSV, external APIs).

## Testing Strategy

- **Unit tests:** Each component in isolation — Strategy receives mock ctx, RiskEngine tested with fake orders/portfolio, Portfolio math verified with known inputs, metrics validated against pre-computed values
- **Integration tests:** End-to-end with pre-loaded data fixture — run a MACrossover strategy, verify equity curve sum equals PnL from individual trades, verify risk rules reject oversized orders
- **Benchmarks:** Engine throughput (bars/second) measured for 100-stock portfolio over 5 years of minute data
- **Coverage:** >90% on engine core, >85% on strategy/risk/metrics

## Explicit Deferrals (out of scope)

- Live/paper trading execution (Phase 3)
- Parameter optimization infrastructure (grid search, genetic algorithms — Phase 2b)
- Walk-forward cross-validation (Phase 2b)
- Factor model research framework (Phase 2b)
- Interactive web dashboard (not in roadmap)
- Multi-asset support beyond equities (Phase 3+)
