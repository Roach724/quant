"""Integration tests for crypto end-to-end flow."""
import asyncio
import pytest
import pandas as pd
import numpy as np
from oms.broker.crypto_broker import CryptoPaperBroker
from oms.manager import OrderManager
from oms.bridge import convert_signal, forward_signal
from engine.strategy import Strategy, Signal
from engine.data import DataFrameSource
from engine.config import BacktestConfig
from engine.engine import Engine


def test_crypto_signal_to_broker_flow():
    """Verify a crypto buy signal flows through bridge -> order manager -> broker."""
    broker = CryptoPaperBroker(initial_capital=10_000.0)
    broker.update_price("BTCUSDT", 65000.0)
    mgr = OrderManager(broker)
    signal_dict = {"symbol": "BTCUSDT", "side": "buy", "qty": 0.01}
    results = forward_signal(signal_dict, broker, mgr)
    assert len(results) == 1
    assert results[0].symbol == "BTCUSDT"
    assert results[0].state == "FILLED"
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
            # Access close from the strategy-level reference, not the outer scope
            current_price = ctx.data.close.iloc[bar]["BTCUSDT"]
            ma_price = self.ma.iloc[bar]["BTCUSDT"]
            if current_price > ma_price:
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


def test_crypto_multiple_symbols_backtest():
    """Run a multi-symbol crypto equity strategy."""
    dates = pd.date_range("2026-01-01", periods=100, freq="1h")
    np.random.seed(123)
    close = pd.DataFrame({
        "BTCUSDT": 65000 + np.cumsum(np.random.randn(100) * 100),
        "ETHUSDT": 3000 + np.cumsum(np.random.randn(100) * 10),
    }, index=dates)
    data = DataFrameSource(close=close)

    class EqualWeightMomentum(Strategy):
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

    cfg = BacktestConfig(initial_capital=50_000, slippage_bps=5, commission_bps=5)
    result = Engine(cfg).run(EqualWeightMomentum(), data)

    from engine.metrics import summary
    s = summary(result)
    assert s["total_trades"] >= 0
    assert isinstance(s["sharpe_ratio"], float)
    # Should have valid equity curve
    eq = result.portfolio.equity_curve
    assert len(eq) > 0
    assert eq.iloc[-1] > 0  # no bankruptcy
