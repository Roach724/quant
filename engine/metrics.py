import numpy as np


def summary(result) -> dict:
    eq = result.portfolio.equity_curve
    rets = eq.pct_change().dropna()
    if len(rets) < 2:
        return _empty_summary()

    total_ret = (eq.iloc[-1] / eq.iloc[0]) - 1
    n_years = max((eq.index[-1] - eq.index[0]).days / 365.25, 0.01)
    ann_ret = (1 + total_ret) ** (1 / n_years) - 1

    avg_ret = rets.mean()
    std_ret = rets.std()
    sharpe = (avg_ret / std_ret) * np.sqrt(252) if std_ret > 0 else 0.0

    downside = rets[rets < 0]
    sortino = (avg_ret / downside.std()) * np.sqrt(252) if len(downside) > 0 and downside.std() > 0 else 0.0

    rolling_max = eq.expanding().max()
    drawdowns = (eq - rolling_max) / rolling_max
    max_dd = drawdowns.min()

    ann_vol = std_ret * np.sqrt(252)
    calmar = ann_ret / abs(max_dd) if abs(max_dd) > 0 else 0.0

    var_95 = np.percentile(rets, 5)
    tail = rets[rets <= var_95]
    cvar_95 = tail.mean() if len(tail) > 0 else var_95

    win_rate = (rets > 0).mean()
    win_sum = rets[rets > 0].sum()
    loss_sum = abs(rets[rets < 0].sum())
    profit_factor = win_sum / loss_sum if loss_sum > 0 else 0.0

    return {
        "total_return": round(total_ret, 4),
        "annual_return": round(ann_ret, 4),
        "sharpe_ratio": round(sharpe, 2),
        "sortino_ratio": round(sortino, 2),
        "max_drawdown": round(max_dd, 4),
        "calmar_ratio": round(calmar, 2),
        "volatility_annual": round(ann_vol, 4),
        "win_rate": round(win_rate, 4),
        "profit_factor": round(profit_factor, 2),
        "avg_trade_pnl": 0.0,
        "total_trades": 0,
        "var_95": round(var_95, 4),
        "cvar_95": round(cvar_95, 4),
    }


def _empty_summary():
    return {k: 0.0 for k in [
        "total_return", "annual_return", "sharpe_ratio", "sortino_ratio",
        "max_drawdown", "calmar_ratio", "volatility_annual", "win_rate",
        "profit_factor", "avg_trade_pnl", "var_95", "cvar_95",
    ]} | {"total_trades": 0}
