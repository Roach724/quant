"""ATRTrailingStop — volatility-adaptive trailing-stop trend-following strategy.

Enters on a simple trend signal (price above MA) and exits when price
drops below a trailing stop that adapts to market volatility via ATR.
"""
from __future__ import annotations

import logging
import numpy as np

from engine.strategy import Strategy, Signal

logger = logging.getLogger(__name__)


def _atr(high: np.ndarray, low: np.ndarray, close: np.ndarray, period: int) -> np.ndarray:
    """Compute Average True Range (Wilder's smoothing)."""
    n = len(close)
    tr = np.zeros(n, dtype=float)
    for i in range(1, n):
        tr[i] = max(
            high[i] - low[i],
            abs(high[i] - close[i - 1]),
            abs(low[i] - close[i - 1]),
        )
    atr = np.full(n, np.nan, dtype=float)
    if n <= period:
        return atr
    # First ATR = simple average of first 'period' TR values
    atr[period] = np.mean(tr[1:period + 1])
    # Wilder's smoothing for the rest
    alpha = 1.0 / period
    for i in range(period + 1, n):
        atr[i] = alpha * tr[i] + (1 - alpha) * atr[i - 1]
    return atr


class ATRTrailingStop(Strategy):
    """ATR-based trailing-stop trend-following strategy.

    Entry: price crosses above entry MA.
    Exit:  price drops below (highest high since entry) - multiplier × ATR.

    Parameters
    ----------
    atr_period : int
        ATR lookback period (default 20).
    multiplier : float
        Trailing stop multiplier (default 3.0).
    entry_ma : int
        Entry signal MA period (default 50).
    top_k : int
        Max concurrent positions.
    """

    atr_period: int = 20
    multiplier: float = 3.0
    entry_ma: int = 50
    top_k: int = 5
    allocation: float = 0.0

    def __init__(self, **kwargs):
        super().__init__()
        for k, v in kwargs.items():
            if hasattr(self, k):
                setattr(self, k, v)

    def on_init(self, ctx):
        # Track per-position: entry bar index, highest high since entry
        self._entry_bar: dict[str, int] = {}
        self._highest_high: dict[str, float] = {}

    def on_bar(self, ctx, bar: int) -> list[Signal]:
        min_bars = max(self.atr_period + 5, self.entry_ma + 5)
        if bar < min_bars:
            return []

        signals: list[Signal] = []

        # ── Compute ATR + MA for each symbol ──
        atr_vals: dict[str, float] = {}
        ma_vals: dict[str, float] = {}
        closes: dict[str, float] = {}
        highs: dict[str, float] = {}

        for sym in ctx.universe:
            try:
                close_s = ctx.data.close[sym].iloc[: bar + 1]
                high_s = (ctx.data.high[sym].iloc[: bar + 1]
                          if hasattr(ctx.data, 'high') and ctx.data.high is not None
                          else close_s)
                low_s = (ctx.data.low[sym].iloc[: bar + 1]
                         if hasattr(ctx.data, 'low') and ctx.data.low is not None
                         else close_s)
                prices = close_s.dropna().values.astype(float)
                if len(prices) < self.entry_ma:
                    continue
                closes[sym] = prices[-1]
                highs[sym] = high_s.dropna().values.astype(float)[-1]
                ma_vals[sym] = float(np.mean(prices[-self.entry_ma:]))
                # ATR
                hp = high_s.dropna().values.astype(float)
                lp = low_s.dropna().values.astype(float)
                cp = close_s.dropna().values.astype(float)
                a = _atr(hp, lp, cp, self.atr_period)
                if not np.isnan(a[-1]):
                    atr_vals[sym] = float(a[-1])
            except (KeyError, IndexError, TypeError):
                continue

        # ── Exit: trailing stop hit ──
        for sym, pos in list(ctx.portfolio.positions.items()):
            if not (hasattr(pos, "size") and pos.size > 0):
                continue
            if sym not in self._highest_high or sym not in self._entry_bar:
                continue
            # Update highest high
            cur_high = highs.get(sym, closes.get(sym, 0))
            if cur_high > self._highest_high[sym]:
                self._highest_high[sym] = cur_high
            # Check stop
            stop_price = self._highest_high[sym] - self.multiplier * atr_vals.get(sym, 0)
            cur_close = closes.get(sym, 0)
            if cur_close <= stop_price:
                signals.append(Signal.close(sym))
                del self._entry_bar[sym]
                del self._highest_high[sym]

        # ── Entry: price above entry MA ──
        candidates: list[tuple[str, float]] = []
        for sym in closes:
            if ctx.portfolio.has_position(sym):
                continue
            ma = ma_vals.get(sym)
            if ma is None or ma <= 0:
                continue
            # Price just crossed above MA (current > MA, prev < MA on last bar)
            prev_close = None
            try:
                prev_close = float(ctx.data.close[sym].iloc[bar - 1])
            except (KeyError, IndexError, TypeError):
                pass
            if closes[sym] > ma and (prev_close is None or prev_close <= ma):
                strength = (closes[sym] - ma) / ma  # % above MA
                candidates.append((sym, strength))

        candidates.sort(key=lambda x: x[1], reverse=True)
        selected = candidates[: self.top_k]

        weight = self.allocation if self.allocation > 0 else (1.0 / max(self.top_k, 1))
        for sym, strength in selected:
            signals.append(Signal.buy(sym, weight=weight, score=float(strength)))
            self._entry_bar[sym] = bar
            self._highest_high[sym] = highs.get(sym, closes.get(sym, 0))

        return signals
