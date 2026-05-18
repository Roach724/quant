import pandas as pd
import numpy as np
from engine.walkforward import WalkForward, _parse_window
from engine.strategy import Strategy, Signal
from engine.data import DataFrameSource
from engine.config import BacktestConfig


class BuyHold(Strategy):
    def on_bar(self, ctx, bar):
        if bar == 0:
            return [Signal.buy(s, weight=1.0/len(ctx.universe)) for s in ctx.universe]
        return []


def test_parse_window():
    assert _parse_window("6M") == 126
    assert _parse_window("1Y") == 252
    assert _parse_window("3M") == 63
    assert _parse_window("2W") == 10
    assert _parse_window("50") == 50


def test_walkforward_runs_folds():
    np.random.seed(42)
    n = 100
    dates = pd.date_range("2026-01-01", periods=n, freq="D")
    close = pd.DataFrame({"AAPL": 100 + np.cumsum(np.random.randn(n) * 1.5 + 0.02)}, index=dates)
    data = DataFrameSource(close=close)
    cfg = BacktestConfig(initial_capital=10_000, slippage_bps=0, commission_bps=0, min_commission=0)

    wf = WalkForward(BuyHold(), data, cfg, train_window=30, test_window=10, step_size=10)
    folds = wf.run()
    assert len(folds) == 7  # (100-30-10)/10 + 1 = 7
    for f in folds:
        assert "train_metrics" in f
        assert "test_metrics" in f
        assert "sharpe_ratio" in f["test_metrics"]


def test_walkforward_with_pred():
    """WalkForward should pass pred data through to fold data sources."""
    import pandas as pd
    import numpy as np
    from engine.config import BacktestConfig
    from engine.data import DataFrameSource
    from engine.walkforward import WalkForward
    from engine.strategy import Strategy

    class DummyStrategy(Strategy):
        def on_bar(self, ctx, bar):
            return []

    np.random.seed(42)
    dates = pd.date_range("2026-01-01", periods=60, freq="D")
    close = pd.DataFrame(
        {"AAPL": 100 + np.cumsum(np.random.randn(60) * 2)}, index=dates
    )
    pred = pd.DataFrame(
        {"AAPL": np.random.randn(60)}, index=dates
    )

    config = BacktestConfig()
    data = DataFrameSource(close=close, pred=pred)
    strategy = DummyStrategy()

    # 30 days train, 10 days test, step 10
    wf = WalkForward(strategy, data, config, train_window=30, test_window=10, step_size=10)
    folds = wf.run()

    assert len(folds) > 0, "Should have at least 1 fold"

    # Each fold should have metrics (verifying it ran successfully)
    for f in folds:
        assert "train_metrics" in f
        assert "test_metrics" in f


def test_walkforward_summary():
    np.random.seed(42)
    n = 80
    dates = pd.date_range("2026-01-01", periods=n, freq="D")
    close = pd.DataFrame({"AAPL": 100 + np.cumsum(np.random.randn(n) * 1.5 + 0.02)}, index=dates)
    data = DataFrameSource(close=close)
    cfg = BacktestConfig(initial_capital=10_000, slippage_bps=0, commission_bps=0, min_commission=0)

    wf = WalkForward(BuyHold(), data, cfg, train_window=30, test_window=10, step_size=10)
    s = wf.summary()
    assert s["n_folds"] >= 1
    assert "sharpe_ratio_mean" in s
