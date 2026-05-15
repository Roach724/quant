import pandas as pd
import numpy as np
from engine.optimize import GridSearch, RandomSearch
from engine.strategy import Strategy, Signal
from engine.data import DataFrameSource
from engine.config import BacktestConfig


class SimpleMA(Strategy):
    fast: int = 10
    slow: int = 30
    def on_init(self, ctx):
        self.ma_fast = ctx.data.close.rolling(self.fast).mean()
        self.ma_slow = ctx.data.close.rolling(self.slow).mean()
    def on_bar(self, ctx, bar):
        if bar < self.slow:
            return []
        for sym in ctx.universe:
            if self.ma_fast.iloc[bar][sym] > self.ma_slow.iloc[bar][sym]:
                if not ctx.portfolio.has_position(sym):
                    return [Signal.buy(sym, weight=1.0)]
            else:
                if ctx.portfolio.has_position(sym):
                    return [Signal.close(sym)]
        return []


def make_data():
    np.random.seed(42)
    dates = pd.date_range("2026-01-01", periods=100, freq="D")
    close = pd.DataFrame({
        "AAPL": 100 + np.cumsum(np.random.randn(100) * 1.5 + 0.02),
    }, index=dates)
    return DataFrameSource(close=close)


def test_grid_search_runs_and_sorts():
    data = make_data()
    cfg = BacktestConfig(initial_capital=10_000, slippage_bps=0, commission_bps=0, min_commission=0)
    gs = GridSearch(SimpleMA, {"fast": [5, 10], "slow": [20, 30]}, data, cfg, metric="sharpe_ratio")
    results = gs.run()
    assert len(results) == 4
    # Check sorted by sharpe descending
    sharpes = [r[1]["sharpe_ratio"] for r in results]
    assert sharpes == sorted(sharpes, reverse=True)


def test_random_search_runs():
    data = make_data()
    cfg = BacktestConfig(initial_capital=10_000, slippage_bps=0, commission_bps=0, min_commission=0)
    rs = RandomSearch(SimpleMA, {"fast": [5, 10, 20], "slow": [20, 30, 40]}, data, cfg, n_iter=10, metric="sharpe_ratio")
    results = rs.run()
    assert len(results) == 10
    for params, metrics in results:
        assert "fast" in params
        assert "slow" in params
        assert "sharpe_ratio" in metrics
