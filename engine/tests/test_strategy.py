from engine.strategy import Strategy, Signal, StrategyContext
from engine.data import DataFrameSource
from engine.portfolio import Portfolio
from engine.config import BacktestConfig
import pandas as pd


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

    def on_init(self, ctx):
        pass

    def on_bar(self, ctx, bar):
        return []


def test_strategy_params_discovery():
    s = TestStrategy()
    params = s.parameters()
    assert params == {"fast": 10, "slow": 30}


def test_strategy_context():
    dates = pd.date_range("2026-01-01", periods=5, freq="D")
    close = pd.DataFrame({"AAPL": [100.0] * 5}, index=dates)
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
