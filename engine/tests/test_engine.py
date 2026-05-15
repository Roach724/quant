from engine.engine import Engine, Result
from engine.config import BacktestConfig
from engine.strategy import Strategy, Signal
from engine.data import DataFrameSource
from engine.portfolio import Portfolio
import pandas as pd


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

    class FakePos:
        symbol = "AAPL"
        size = 100

    pf.positions["AAPL"] = FakePos()
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
            return [Signal.buy(s, weight=1.0 / len(ctx.universe)) for s in ctx.universe]
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
    pf = Portfolio(100_000)
    cfg = BacktestConfig()
    r = Result(portfolio=pf, config=cfg, strategy_name="test")
    assert r.strategy_name == "test"
    assert r.config is cfg
