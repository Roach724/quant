"""Integration tests for Hong Kong market data through the engine."""
import pytest
import pandas as pd
import numpy as np
from engine.strategy import Strategy, Signal
from engine.data import DataFrameSource
from engine.config import BacktestConfig
from engine.engine import Engine


def test_hk_single_symbol_backtest():
    """Run a momentum strategy on simulated HK data."""
    dates = pd.date_range("2026-01-01", periods=100, freq="1d")
    np.random.seed(42)
    close = pd.DataFrame({
        "0700": 380 + np.cumsum(np.random.randn(100) * 3 + 0.05),
    }, index=dates)
    data = DataFrameSource(close=close)

    class TencentMomentum(Strategy):
        lookback: int = 20

        def on_init(self, ctx):
            self.ma = ctx.data.close.rolling(self.lookback).mean()

        def on_bar(self, ctx, bar):
            if bar < self.lookback:
                return []
            price = ctx.data.close.iloc[bar]["0700"]
            ma_price = self.ma.iloc[bar]["0700"]
            if price > ma_price:
                if not ctx.portfolio.has_position("0700"):
                    return [Signal.buy("0700", weight=1.0)]
            else:
                if ctx.portfolio.has_position("0700"):
                    return [Signal.close("0700")]
            return []

    cfg = BacktestConfig(initial_capital=500_000, slippage_bps=5, commission_bps=3,
                         min_commission=15.0)  # HK min commission ~HKD 15
    result = Engine(cfg).run(TencentMomentum(), data)

    from engine.metrics import summary
    s = summary(result)
    assert s["total_trades"] >= 0
    assert isinstance(s["sharpe_ratio"], float)
    assert len(result.portfolio.equity_curve) == 100
    assert result.portfolio.equity_curve.iloc[-1] > 0


def test_hk_multi_symbol_basket():
    """Run multi-symbol HK equal-weight basket strategy."""
    dates = pd.date_range("2026-01-01", periods=80, freq="1d")
    np.random.seed(123)
    close = pd.DataFrame({
        "0700": 380 + np.cumsum(np.random.randn(80) * 3),
        "9988": 80 + np.cumsum(np.random.randn(80) * 1.5),
        "0388": 280 + np.cumsum(np.random.randn(80) * 2),
    }, index=dates)
    data = DataFrameSource(close=close)

    class HKBasket(Strategy):
        lookback: int = 10

        def on_init(self, ctx):
            self.returns = ctx.data.close.pct_change(self.lookback)

        def on_bar(self, ctx, bar):
            if bar < self.lookback:
                return []
            signals = []
            for sym in ctx.universe:
                mom = self.returns.iloc[bar][sym]
                if mom > 0 and not ctx.portfolio.has_position(sym):
                    signals.append(Signal.buy(sym, weight=1.0 / len(ctx.universe)))
                elif mom <= 0 and ctx.portfolio.has_position(sym):
                    signals.append(Signal.close(sym))
            return signals

    cfg = BacktestConfig(initial_capital=1_000_000, slippage_bps=5, commission_bps=3,
                         min_commission=15.0)
    result = Engine(cfg).run(HKBasket(), data)

    from engine.metrics import summary
    s = summary(result)
    assert s["total_trades"] >= 0
    eq = result.portfolio.equity_curve
    assert len(eq) > 0
    assert eq.iloc[-1] > 0  # no bankruptcy


def test_hk_adapter_dataframe_directly():
    """Verify YFinanceHKAdapter produces engine-compatible DataFrames."""
    import pandas as pd
    import numpy as np
    from engine.data import DataFrameSource

    # Simulate what the adapter would return after pivot
    dates = pd.date_range("2026-05-01", periods=10, freq="1d")
    df = pd.DataFrame({
        "symbol": (["0700"] * 10 + ["9988"] * 10),
        "timestamp": list(dates) * 2,
        "close": [380 + i * 2 for i in range(10)] + [80 + i * 0.5 for i in range(10)],
        "market": ["HK"] * 20,
        "frequency": ["1d"] * 20,
    })

    # Sdk direct mode would set multi-index
    df = df.set_index(["symbol", "timestamp"]).sort_index()
    pivot = df["close"].unstack(level="symbol")

    src = DataFrameSource(close=pivot)
    assert src.universe == ["0700", "9988"]
    assert len(src) == 10
