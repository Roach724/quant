"""RSI2 — Larry Connors' 2-period RSI mean-reversion strategy.

Ultra-short-term mean reversion: buys extreme RSI oversold readings
(< 10 on 2-period RSI) and closes quickly on exit signals.
"""
from __future__ import annotations

import logging
import numpy as np

from engine.strategy import Strategy, Signal

logger = logging.getLogger(__name__)


def _rsi(close: np.ndarray, period: int) -> np.ndarray:
    """Compute RSI (Relative Strength Index) using Wilder's smoothing."""
    n = len(close)
    if n <= period:
        return np.full(n, np.nan, dtype=float)
    delta = np.diff(close)
    gain = np.where(delta > 0, delta, 0.0)
    loss = np.where(delta < 0, -delta, 0.0)
    rsi = np.full(n, np.nan, dtype=float)
    # Initial average
    avg_gain = np.mean(gain[:period])
    avg_loss = np.mean(loss[:period])
    rsi[period] = 100.0 - 100.0 / (1.0 + avg_gain / max(avg_loss, 1e-10))
    # Wilder's smoothing
    alpha = 1.0 / period
    for i in range(period + 1, n):
        avg_gain = alpha * gain[i - 1] + (1 - alpha) * avg_gain
        avg_loss = alpha * loss[i - 1] + (1 - alpha) * avg_loss
        rsi[i] = 100.0 - 100.0 / (1.0 + avg_gain / max(avg_loss, 1e-10))
    return rsi


class RSI2(Strategy):
    """Larry Connors' 2-period RSI strategy.

    Parameters
    ----------
    rsi_period : int
        RSI lookback period (standard = 2).
    buy_threshold : float
        RSI below this triggers buy (standard = 10, extreme oversold).
    sell_threshold : float
        RSI above this triggers sell (standard = 70).
    top_k : int
        Max concurrent positions.
    max_hold_bars : int
        Force-close after this many bars (default 3 for ultra-short-term).
    """

    rsi_period: int = 2
    buy_threshold: float = 10.0
    sell_threshold: float = 70.0
    top_k: int = 5
    allocation: float = 0.0
    max_hold_bars: int = 3  # ultra-short-term: exit within 3 bars

    def __init__(self, **kwargs):
        super().__init__()
        for k, v in kwargs.items():
            if hasattr(self, k):
                setattr(self, k, v)

    def on_init(self, ctx):
        self._entry_bar: dict[str, int] = {}

    def on_bar(self, ctx, bar: int) -> list[Signal]:
        min_bars = self.rsi_period + 5
        if bar < min_bars:
            return []

        signals: list[Signal] = []
        rsi_vals: dict[str, float] = {}

        for sym in ctx.universe:
            try:
                series = ctx.data.close[sym].iloc[: bar + 1]
                prices = series.dropna().values.astype(float)
                if len(prices) <= self.rsi_period:
                    continue
                rsi_arr = _rsi(prices, self.rsi_period)
                if not np.isnan(rsi_arr[-1]):
                    rsi_vals[sym] = float(rsi_arr[-1])
            except (KeyError, IndexError, TypeError):
                continue

        # ── Exit: RSI overbought OR timeout ──
        for sym, pos in list(ctx.portfolio.positions.items()):
            if not (hasattr(pos, "size") and pos.size > 0):
                continue
            rsi = rsi_vals.get(sym)
            if rsi is None:
                continue
            # Exit 1: overbought → take profit
            if rsi >= self.sell_threshold:
                signals.append(Signal.close(sym))
                self._entry_bar.pop(sym, None)
            # Exit 2: timeout (ultra-short-term strategy)
            elif sym in self._entry_bar and bar - self._entry_bar[sym] >= self.max_hold_bars:
                signals.append(Signal.close(sym))
                del self._entry_bar[sym]

        # ── Entry: extreme oversold ──
        candidates: list[tuple[str, float]] = []
        for sym, rsi in rsi_vals.items():
            if ctx.portfolio.has_position(sym):
                continue
            if rsi <= self.buy_threshold:
                # Score: lower RSI = more extreme oversold
                candidates.append((sym, self.buy_threshold - rsi))

        candidates.sort(key=lambda x: x[1], reverse=True)
        selected = candidates[: self.top_k]

        weight = self.allocation if self.allocation > 0 else (1.0 / max(self.top_k, 1))
        for sym, score in selected:
            signals.append(Signal.buy(sym, weight=weight, score=float(score)))
            self._entry_bar[sym] = bar

        return signals
