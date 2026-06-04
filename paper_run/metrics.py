"""Performance metrics computed from equity curve data.

All functions accept a list of equity values (chronological) and return
a dict or scalar. Equity values are portfolio_value including cash.
"""

from __future__ import annotations

import math
from typing import Sequence


def compute_all_metrics(
    equity_series: Sequence[float],
    risk_free_rate: float = 0.02,
    periods_per_year: int = 252,
) -> dict:
    """Compute all standard performance metrics from an equity curve.

    Args:
        equity_series: chronological portfolio values (includes cash).
        risk_free_rate: annual risk-free rate (default 2%).
        periods_per_year: trading periods per year (252 for daily, 78 for 5m).

    Returns dict with keys:
        total_return, annual_return, annual_vol, sharpe, sortino,
        max_drawdown, calmar, win_rate, total_trades, profit_factor,
        start_equity, end_equity, n_periods
    """
    n = len(equity_series)
    if n < 2:
        return {
            "total_return": 0.0, "annual_return": 0.0, "annual_vol": 0.0,
            "sharpe": 0.0, "sortino": 0.0, "max_drawdown": 0.0,
            "calmar": 0.0, "win_rate": 0.0, "total_trades": 0,
            "profit_factor": 0.0, "start_equity": equity_series[0] if n else 0,
            "end_equity": equity_series[-1] if n else 0, "n_periods": n,
        }

    returns = _compute_returns(equity_series)
    total_return = (equity_series[-1] / equity_series[0]) - 1
    annual_return = _annualized_return(total_return, n, periods_per_year)
    annual_vol = _annualized_vol(returns, periods_per_year)
    sharpe = _sharpe_ratio(returns, risk_free_rate, periods_per_year, annual_vol)
    sortino = _sortino_ratio(returns, risk_free_rate, periods_per_year)
    max_dd = _max_drawdown(equity_series)
    calmar = annual_return / abs(max_dd) if max_dd != 0 else 0.0
    win_rate, total_trades, profit_factor = _trade_stats(returns)

    return {
        "total_return": round(total_return, 6),
        "annual_return": round(annual_return, 6),
        "annual_vol": round(annual_vol, 6),
        "sharpe": round(sharpe, 4),
        "sortino": round(sortino, 4),
        "max_drawdown": round(max_dd, 6),
        "calmar": round(calmar, 4),
        "win_rate": round(win_rate, 4),
        "total_trades": total_trades,
        "profit_factor": round(profit_factor, 4),
        "start_equity": round(equity_series[0], 2),
        "end_equity": round(equity_series[-1], 2),
        "n_periods": n,
    }


def _compute_returns(equity: Sequence[float]) -> list[float]:
    """Period-over-period returns."""
    return [(equity[i] / equity[i - 1]) - 1 for i in range(1, len(equity))]


def _annualized_return(total_return: float, n_periods: int, periods_per_year: int) -> float:
    """CAGR: (1 + total_return)^(periods_per_year / n_periods) - 1."""
    if n_periods == 0:
        return 0.0
    return (1 + total_return) ** (periods_per_year / n_periods) - 1


def _annualized_vol(returns: list[float], periods_per_year: int) -> float:
    """Std of returns * sqrt(periods_per_year)."""
    if len(returns) < 2:
        return 0.0
    mean_r = sum(returns) / len(returns)
    variance = sum((r - mean_r) ** 2 for r in returns) / (len(returns) - 1)
    return math.sqrt(variance) * math.sqrt(periods_per_year)


def _sharpe_ratio(
    returns: list[float], risk_free_rate: float,
    periods_per_year: int, annual_vol: float,
) -> float:
    """Sharpe = (annual_return - risk_free_rate) / annual_vol."""
    if annual_vol == 0 or len(returns) < 2:
        return 0.0
    n = len(returns)
    total_return = 1.0
    for r in returns:
        total_return *= (1 + r)
    annual_r = total_return ** (periods_per_year / n) - 1
    return (annual_r - risk_free_rate) / annual_vol


def _sortino_ratio(
    returns: list[float], risk_free_rate: float, periods_per_year: int,
) -> float:
    """Sortino = (annual_return - risk_free_rate) / downside_deviation."""
    if len(returns) < 2:
        return 0.0
    n = len(returns)
    total_return = 1.0
    for r in returns:
        total_return *= (1 + r)
    annual_r = total_return ** (periods_per_year / n) - 1

    # Downside deviation (only negative returns)
    period_rf = risk_free_rate / periods_per_year
    downside = [min(r - period_rf, 0) ** 2 for r in returns]
    if len(downside) < 2:
        return 0.0
    downside_std = math.sqrt(sum(downside) / (len(downside) - 1))
    downside_annual = downside_std * math.sqrt(periods_per_year)
    if downside_annual == 0:
        return 0.0
    return (annual_r - risk_free_rate) / downside_annual


def _max_drawdown(equity: Sequence[float]) -> float:
    """Maximum peak-to-trough decline as a negative fraction."""
    peak = equity[0]
    max_dd = 0.0
    for val in equity:
        if val > peak:
            peak = val
        dd = (val - peak) / peak
        if dd < max_dd:
            max_dd = dd
    return max_dd


def _trade_stats(returns: list[float]) -> tuple[float, int, float]:
    """Win rate, total trades (periods with non-zero return), profit factor."""
    wins = sum(1 for r in returns if r > 0)
    losses = sum(1 for r in returns if r < 0)
    total = wins + losses
    win_rate = wins / total if total > 0 else 0.0

    gross_profit = sum(r for r in returns if r > 0)
    gross_loss = abs(sum(r for r in returns if r < 0))
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else 0.0

    return win_rate, total, profit_factor
