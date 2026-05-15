import numpy as np
import pandas as pd
from engine.metrics import summary
from engine.engine import Result
from engine.portfolio import Portfolio
from engine.config import BacktestConfig


def make_portfolio(equity_values):
    pf = Portfolio(equity_values[0])
    pf._equity = list(equity_values)
    pf._timestamps = list(pd.date_range("2026-01-01", periods=len(equity_values), freq="D"))
    return pf


def test_summary_flat_equity():
    pf = make_portfolio([100_000] * 100)
    r = Result(portfolio=pf, config=BacktestConfig())
    s = summary(r)
    assert s["total_return"] == 0.0
    assert s["sharpe_ratio"] == 0.0
    assert s["max_drawdown"] == 0.0


def test_summary_positive_return():
    values = [100_000 + i * 100 for i in range(252)]
    pf = make_portfolio(values)
    r = Result(portfolio=pf, config=BacktestConfig())
    s = summary(r)
    assert s["total_return"] > 0
    assert s["annual_return"] > 0
    assert s["sharpe_ratio"] > 0
    assert s["max_drawdown"] == 0.0


def test_summary_has_all_keys():
    pf = make_portfolio([100_000] * 50)
    r = Result(portfolio=pf, config=BacktestConfig())
    s = summary(r)
    required = ["total_return", "annual_return", "sharpe_ratio", "sortino_ratio",
                "max_drawdown", "calmar_ratio", "volatility_annual", "win_rate",
                "profit_factor", "avg_trade_pnl", "total_trades",
                "var_95", "cvar_95"]
    for k in required:
        assert k in s, f"Missing key: {k}"


def test_max_drawdown():
    values = [100, 110, 90, 95, 105]
    pf = make_portfolio([v * 1000 for v in values])
    r = Result(portfolio=pf, config=BacktestConfig())
    s = summary(r)
    assert s["max_drawdown"] < 0
    assert abs(s["max_drawdown"] - (-18.18 / 100)) < 0.05


def test_sharpe_approximation():
    np.random.seed(42)
    values = [100_000 * (1 + 0.001 * i + 0.005 * np.random.randn()) for i in range(252)]
    pf = make_portfolio(values)
    r = Result(portfolio=pf, config=BacktestConfig())
    s = summary(r)
    assert s["volatility_annual"] > 0
    assert isinstance(s["sharpe_ratio"], float)
