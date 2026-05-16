# Research & Analysis Guide — Strategy Development

How to use the backtesting engine for strategy research, optimization, and factor analysis.

## Quickstart: Your First Backtest

```python
import pandas as pd
import numpy as np
from engine import Strategy, Signal, Engine, BacktestConfig, DataFrameSource, summary

# 1. Define a strategy
class MACrossover(Strategy):
    fast: int = 20
    slow: int = 50

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
                    signals.append(Signal.buy(sym, weight=1.0/len(ctx.universe)))
            else:
                if ctx.portfolio.has_position(sym):
                    signals.append(Signal.close(sym))
        return signals

# 2. Prepare data
dates = pd.date_range("2024-01-01", periods=504, freq="D")
close = pd.DataFrame({
    "SPY": 450 + np.cumsum(np.random.randn(504) * 1.5 + 0.03),
    "QQQ": 380 + np.cumsum(np.random.randn(504) * 2.0 + 0.04),
}, index=dates)
data = DataFrameSource(close=close)

# 3. Run backtest
cfg = BacktestConfig(initial_capital=100_000, slippage_bps=5, commission_bps=1, min_commission=1.0)
result = Engine(cfg).run(MACrossover(), data)

# 4. Analyze
s = summary(result)
for k, v in s.items():
    print(f"  {k}: {v}")
```

**Expected output:**
```
  total_return: 0.1243
  annual_return: 0.0612
  sharpe_ratio: 0.82
  max_drawdown: -0.0531
  win_rate: 0.562
  ...
```

## The Strategy API

### class Strategy

Your strategy subclasses `Strategy`. Two methods to implement:

| Method | Called | Purpose |
|--------|--------|---------|
| `on_init(self, ctx)` | Once, before first bar | Pre-compute indicators, register risk rules |
| `on_bar(self, ctx, bar)` | Every bar | Return list of Signal objects |

### StrategyContext (ctx)

| Attribute | Type | Description |
|-----------|------|-------------|
| `ctx.data` | DataSource | Market data (close, open, high, low, volume) |
| `ctx.portfolio` | Portfolio | Current positions, cash, equity |
| `ctx.config` | BacktestConfig | Slippage, commission, initial capital |
| `ctx.universe` | list[str] | Symbols in the data |

### Signal types

```python
Signal.buy("AAPL", weight=0.5)       # Open a 50% position
Signal.sell("AAPL")                   # Close entire position
Signal.close("AAPL")                  # Same as sell
Signal.target("AAPL", weight=0.3)     # Rebalance to 30% (buys or sells as needed)

# Advanced: explicit quantity
Signal.buy("AAPL", weight=0.5)
signal.qty = 200                     # Override computed size
signal.order_type = "limit"
signal.limit_price = 148.50
```

### Parameters

Class attributes are auto-discovered for optimization:

```python
class MyStrategy(Strategy):
    fast: int = 20     # ← These are parameters
    slow: int = 50     # ← that GridSearch can tune
```

## BacktestConfig

```python
cfg = BacktestConfig(
    initial_capital=100_000,    # Starting portfolio value
    slippage_bps=5,             # 5 basis points = 0.05% slippage
    commission_bps=1,           # 1 bp = 0.01% commission
    min_commission=1.0,         # Minimum $1 per trade
    benchmark_symbol="SPY",     # For beta/alpha calculation
)
```

## Risk Rules in Backtesting

Add risk controls to backtests for realistic simulation:

```python
from engine.risk.stop_loss import StopLoss
from engine.risk.exposure import ExposureLimit

class MyStrategy(Strategy):
    def on_init(self, ctx):
        self.add_risk(StopLoss(pct=0.05))         # -5% stop per position
        self.add_risk(ExposureLimit(max_pct=0.25)) # Max 25% per position
        ...
```

**Available rules:**

| Rule | Parameter | Description |
|------|-----------|-------------|
| `StopLoss(pct=0.05)` | `pct` | Close position if loss > 5% |
| `ExposureLimit(max_pct=0.25)` | `max_pct` | Cap any position at 25% of equity |
| `MaxDrawdown(limit=0.20)` | `limit` | Reject new orders if down 20% from peak |
| `MaxLeverage(limit=1.5)` | `limit` | Block orders exceeding 1.5x leverage |
| `VolatilityTarget(annual=0.15)` | `annual` | Scale positions to target 15% annual vol |

## Performance Metrics

`summary(result)` returns a dict with these keys:

| Metric | Description |
|--------|-------------|
| `total_return` | Cumulative return over the full period |
| `annual_return` | CAGR (compound annual growth rate) |
| `sharpe_ratio` | Return per unit of risk (annualized) |
| `sortino_ratio` | Like Sharpe but only penalizes downside vol |
| `max_drawdown` | Worst peak-to-trough decline |
| `calmar_ratio` | Annual return / max drawdown |
| `volatility_annual` | Standard deviation of daily returns × √252 |
| `win_rate` | Fraction of bars with positive return |
| `profit_factor` | Gross profit / gross loss |
| `var_95` | 95% daily Value at Risk |
| `cvar_95` | Conditional VaR (expected loss in tail) |

## Parameter Optimization

Don't guess parameters — search for the best ones.

### GridSearch

```python
from engine.optimize import GridSearch

gs = GridSearch(
    strategy_class=MACrossover,
    param_grid={"fast": [10, 20, 30], "slow": [40, 50, 60]},
    data=data, config=cfg,
    metric="sharpe_ratio",
)
results = gs.run()  # Sorted best → worst
best_params, best_metrics = results[0]
print(f"Best: fast={best_params['fast']}, slow={best_params['slow']}")
print(f"Sharpe: {best_metrics['sharpe_ratio']:.2f}")
```

### RandomSearch

For larger parameter spaces:

```python
from engine.optimize import RandomSearch

rs = RandomSearch(
    MACrossover,
    param_grid={"fast": [5, 10, 15, 20, 30, 50], "slow": [20, 40, 60, 100, 150]},
    data=data, config=cfg,
    n_iter=100,       # Try 100 random combinations
    metric="sharpe_ratio",
)
```

**Warning:** Optimization without validation leads to overfitting. Always follow with walk-forward.

## Walk-Forward Cross-Validation

The gold standard for strategy validation:

```python
from engine.walkforward import WalkForward

wf = WalkForward(
    strategy=MACrossover(),
    data=data, config=cfg,
    train_window="6M",    # Train on 6 months
    test_window="1M",     # Test on 1 month out-of-sample
)
folds = wf.run()

# Aggregate out-of-sample performance
oos = wf.summary()
print(f"OOS Sharpe (mean): {oos['sharpe_ratio_mean']:.2f}")
print(f"OOS Return (mean): {oos['annual_return_mean']:.4f}")
print(f"Folds: {oos['n_folds']}")

# Inspect individual folds
for f in folds:
    print(f"Fold {f['fold']}: train={f['train_start'][:10]}..{f['train_end'][:10]} "
          f"test={f['test_start'][:10]}..{f['test_end'][:10]} "
          f"Sharpe={f['test_metrics']['sharpe_ratio']:.2f}")
```

**Interpreting walk-forward results:**
- Consistent positive OOS Sharpe across all folds → robust strategy
- High in-sample, low out-of-sample → overfitting
- Large variance in OOS Sharpe across folds → unstable, parameter-sensitive

## Factor Research

Define factors and compute their predictive power:

```python
from engine.factors import Factor, compute_ic, factor_returns

# Define factors
momentum = Factor("momentum", lambda df: df.pct_change(20))
volatility = Factor("volatility", lambda df: df.pct_change().rolling(20).std())

# Compute Information Coefficient (Spearman rank correlation with forward returns)
fwd_5d = data.close.pct_change(5).shift(-5)  # 5-day forward returns
ic_results = compute_ic([momentum, volatility], fwd_5d, data)

for name, ic_series in ic_results.items():
    print(f"{name}: mean IC = {ic_series.mean():.4f}, "
          f"IC IR = {ic_series.mean()/ic_series.std():.2f}")

# Factor returns attribution
result = Engine(cfg).run(MyStrategy(), data)
port_rets = result.portfolio.returns
exposures = pd.DataFrame({
    "momentum": data.close.pct_change(20).stack().unstack().mean(axis=1)[:len(port_rets)],
    "volatility": data.close.pct_change().rolling(20).std().stack().unstack().mean(axis=1)[:len(port_rets)],
})
attr = factor_returns(port_rets, exposures)
print(f"Alpha: {attr['alpha']:.6f}")
print(f"Betas: {attr['betas']}")
print(f"R²: {attr['r_squared']:.4f}")
```

**What good IC looks like:**
- |IC| > 0.05: potentially useful
- |IC| > 0.10: strong predictive power
- IC IR (IC mean / IC std) > 0.5: consistent signal

## Generating Reports

Produce an HTML tear sheet for presentation or archiving:

```python
from engine.report import report_generate

report_generate(result, "my_strategy_report.html")
```

The report includes:
- Metrics summary cards
- Equity curve with benchmark overlay
- Drawdown chart
- Monthly returns bar chart

## Data Pipeline Integration

Load production data from Phase 1:

```python
# Method 1: Direct GCS read (fast, for large queries)
from quant.direct import bars_direct
df = bars_direct(["SPY", "QQQ"], "2024-01-01", "2026-01-01", market="us")

# Method 2: Via Go query API (requires credentials)
from quant import bars
df = bars(["SPY", "QQQ"], "2024-01-01", "2026-01-01")

# Method 3: BigQuery (SQL analytics)
# SELECT * FROM quant.us_bars WHERE symbol = 'SPY'

# Use with the engine:
from engine.data import DataFrameSource
data = DataFrameSource(close=df.pivot_table(values="close", index="timestamp", columns="symbol"))
```
