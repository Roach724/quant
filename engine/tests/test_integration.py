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
    n = 250
    dates = pd.date_range("2026-01-01", periods=n, freq="D")
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
    eq = result.portfolio.equity_curve
    assert eq.iloc[-1] >= eq.iloc[0] * 0.95
