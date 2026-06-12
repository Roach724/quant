"""MACrossover — dual moving-average crossover trend-following strategy.

Buys when the fast MA crosses above the slow MA (golden cross), closes
on death cross.  Simpler and faster to compute than MACD.
"""
from __future__ import annotations

import logging
import numpy as np

from engine.strategy import Strategy, Signal

logger = logging.getLogger(__name__)


class MACrossover(Strategy):
    """Dual simple-moving-average crossover strategy.

    Parameters
    ----------
    fast : int
        Fast SMA period (default 10).
    slow : int
        Slow SMA period (default 30).
    top_k : int
        Max concurrent positions.
    """

    fast: int = 10
    slow: int = 30
    top_k: int = 5
    allocation: float = 0.0

    def __init__(self, **kwargs):
        super().__init__()
        for k, v in kwargs.items():
            if hasattr(self, k):
                setattr(self, k, v)

    def on_init(self, ctx):
        self._prev_fast: dict[str, float] = {}
        self._prev_slow: dict[str, float] = {}

    def on_bar(self, ctx, bar: int) -> list[Signal]:
        if bar < self.slow:
            return []

        signals: list[Signal] = []
        fast_ma: dict[str, float] = {}
        slow_ma: dict[str, float] = {}

        for sym in ctx.universe:
            try:
                close_series = ctx.data.close[sym].iloc[: bar + 1]
                prices = close_series.dropna().values.astype(float)
                if len(prices) < self.slow:
                    continue
                fast_ma[sym] = float(np.mean(prices[-self.fast:]))
                slow_ma[sym] = float(np.mean(prices[-self.slow:]))
            except (KeyError, IndexError, TypeError):
                continue

        if not fast_ma:
            return []

        # ── Exit: death cross (fast crosses below slow) ──
        for sym, pos in list(ctx.portfolio.positions.items()):
            if not (hasattr(pos, "size") and pos.size > 0):
                continue
            pf = self._prev_fast.get(sym)
            ps = self._prev_slow.get(sym)
            cf = fast_ma.get(sym)
            cs = slow_ma.get(sym)
            if None in (pf, ps, cf, cs):
                continue
            if pf > ps and cf < cs:
                signals.append(Signal.close(sym))

        # ── Entry: golden cross (fast crosses above slow) ──
        crosses: list[tuple[str, float]] = []
        for sym in fast_ma:
            if ctx.portfolio.has_position(sym):
                continue
            pf = self._prev_fast.get(sym)
            ps = self._prev_slow.get(sym)
            cf = fast_ma[sym]
            cs = slow_ma[sym]
            if None in (pf, ps):
                continue
            if pf < ps and cf > cs:
                # Strength = gap between MAs (larger gap = stronger trend)
                crosses.append((sym, cf - cs))

        crosses.sort(key=lambda x: x[1], reverse=True)
        selected = crosses[: self.top_k]

        weight = self.allocation if self.allocation > 0 else (1.0 / max(self.top_k, 1))
        for sym, strength in selected:
            signals.append(Signal.buy(sym, weight=weight, score=float(strength)))

        self._prev_fast = fast_ma
        self._prev_slow = slow_ma

        return signals
