"""Tests for paper_run/metrics.py"""
import sys; sys.path.insert(0, '.')
from paper_run.metrics import compute_all_metrics, _max_drawdown


def test_flat_equity():
    """Flat equity should give zero return, zero vol, zero sharpe."""
    m = compute_all_metrics([100000] * 50)
    assert m["total_return"] == 0.0
    assert m["annual_return"] == 0.0
    assert m["sharpe"] == 0.0
    assert m["max_drawdown"] == 0.0


def test_steady_growth():
    """Steady 0.1% per period should give positive Sharpe, zero drawdown."""
    equity = [100000 * (1 + 0.001) ** i for i in range(252)]
    m = compute_all_metrics(equity, periods_per_year=252)
    assert m["total_return"] > 0
    assert m["sharpe"] > 0
    assert m["max_drawdown"] == 0.0


def test_drawdown():
    """Equity that drops 10% then recovers."""
    equity = [100, 90, 95, 100]
    dd = _max_drawdown(equity)
    assert dd == -0.1


def test_losing_equity():
    """Steady losses should give negative Sharpe."""
    equity = [100000 * (1 - 0.002) ** i for i in range(100)]
    m = compute_all_metrics(equity, periods_per_year=252)
    assert m["total_return"] < 0
    assert m["sharpe"] < 0
    assert m["max_drawdown"] < 0


def test_single_point():
    """Single data point should return zeros."""
    m = compute_all_metrics([100000])
    assert m["sharpe"] == 0.0
    assert m["n_periods"] == 1
