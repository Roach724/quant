"""QARP — Quality At a Reasonable Price composite-score long-only strategy.

Reads a composite score from ctx.predictions (dict of symbol → float),
selects the top_k symbols, and rebalances on schedule.
"""
from __future__ import annotations

import numpy as np

from engine.strategy import Strategy, Signal


class QARP(Strategy):
    """Quality At a Reasonable Price — composite-score long-only strategy.

    Parameters
    ----------
    top_k : int
        Number of symbols to hold.
    rebalance_every : int
        Rebalance frequency in bars (default 21 ≈ monthly).
    """

    top_k: int = 10
    rebalance_every: int = 21

    def on_init(self, ctx):
        self._last_rebalance = -self.rebalance_every
        self._current_selection: set[str] = set()

    def on_bar(self, ctx, bar: int) -> list[Signal]:
        if bar - self._last_rebalance < self.rebalance_every:
            return []

        self._last_rebalance = bar

        if ctx.predictions is None:
            return []

        pred = ctx.predictions

        # Filter to universe and remove NaN
        ranked = [(s, v) for s, v in pred.items()
                  if s in ctx.universe and not np.isnan(v)]
        if not ranked:
            return []

        # Sort by composite score descending, select top-k
        ranked.sort(key=lambda x: x[1], reverse=True)
        selected = {sym for sym, _ in ranked[:self.top_k]}

        signals: list[Signal] = []

        # Close positions no longer selected
        for sym in list(self._current_selection):
            if sym not in selected:
                signals.append(Signal.close(sym))

        # Enter new positions
        weight = 1.0 / max(self.top_k, 1)
        for sym in selected:
            if sym not in self._current_selection:
                signals.append(Signal.buy(sym, weight=weight))

        self._current_selection = selected
        return signals
