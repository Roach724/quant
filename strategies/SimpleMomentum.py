"""SimpleMomentum — classic momentum-based stock selection.

Selects top-K stocks by past N-bar return.  Rebalances every R bars.
"""
from __future__ import annotations

import numpy as np

from engine.strategy import Strategy, Signal


class SimpleMomentum(Strategy):
    """Buy the top-K symbols by recent N-bar return.  Rebalance every R bars.

    Parameters
    ----------
    lookback : int
        Number of bars to compute momentum over.
    top_k : int
        How many symbols to hold at a time.
    rebalance_every : int
        Rebalance frequency in bars.
    allocation : float
        Fraction of equity allocated to each position (1.0 / top_k if 0).
    """

    lookback: int = 20
    top_k: int = 5
    rebalance_every: int = 5
    allocation: float = 0.0

    def on_init(self, ctx):
        self._last_rebalance = -self.rebalance_every  # trigger first bar

    def on_bar(self, ctx, bar: int) -> list[Signal]:
        if bar - self._last_rebalance < self.rebalance_every:
            return []

        if bar < self.lookback:
            return []

        self._last_rebalance = bar

        # Compute momentum score = (price_t / price_{t-N} - 1)
        scores = {}
        for sym in ctx.universe:
            try:
                p_now = ctx.data.close.iloc[bar][sym]
                p_prev = ctx.data.close.iloc[bar - self.lookback][sym]
                if p_prev and p_prev > 0:
                    scores[sym] = float(p_now / p_prev - 1.0)
            except (KeyError, IndexError):
                continue

        # Top-K by score
        ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        selected = {sym for sym, _ in ranked[: self.top_k]}

        signals: list[Signal] = []

        # Exit positions no longer selected
        for sym, pos in ctx.portfolio.positions.items():
            if hasattr(pos, "size") and pos.size > 0 and sym not in selected:
                signals.append(Signal.close(sym))

        # Enter new positions
        weight = self.allocation if self.allocation > 0 else (1.0 / max(self.top_k, 1))
        for sym in selected:
            if not ctx.portfolio.has_position(sym):
                signals.append(Signal.buy(sym, weight=weight))

        return signals
