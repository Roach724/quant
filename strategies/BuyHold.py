"""BuyHold — buy every symbol on the first bar and hold until the end.

Baseline strategy for benchmarking.
"""
from __future__ import annotations

from engine.strategy import Strategy, Signal


class BuyHold(Strategy):
    """Buy every symbol in the universe on the first bar and hold until the end.

    Parameters
    ----------
    weight_per_symbol : float
        Fraction of portfolio equity allocated to each symbol (0–1).
    """

    weight_per_symbol: float = 0.1

    def on_bar(self, ctx, bar: int) -> list[Signal]:
        if bar != 0:
            return []
        return [
            Signal.buy(sym, weight=self.weight_per_symbol)
            for sym in ctx.universe
        ]
