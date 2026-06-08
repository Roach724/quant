"""MeanReversion — buy oversold, sell overbought via RSI-style z-score.

Identifies oversold stocks for entry and overbought positions for exit.
"""
from __future__ import annotations

import numpy as np

from engine.strategy import Strategy, Signal


class MeanReversion(Strategy):
    """Buy top oversold symbols and sell overbought based on RSI-style logic.

    Parameters
    ----------
    lookback : int
        Period for mean / std calculation.
    entry_threshold : float
        Z-score *below* which to buy (e.g. –1.5).
    exit_threshold : float
        Z-score *above* which to sell (e.g. +1.5).
    top_k : int
        Max positions.
    """

    lookback: int = 30
    entry_threshold: float = -1.5
    exit_threshold: float = 1.5
    top_k: int = 5
    allocation: float = 0.0

    def on_bar(self, ctx, bar: int) -> list[Signal]:
        if bar < self.lookback:
            return []

        scores = {}
        for sym in ctx.universe:
            try:
                series = ctx.data.close[sym].iloc[bar - self.lookback : bar + 1]
                mu = float(series.mean())
                sigma = float(series.std())
                if sigma == 0:
                    continue
                z = float((series.iloc[-1] - mu) / sigma)
                scores[sym] = z
            except (KeyError, IndexError):
                continue

        signals: list[Signal] = []

        # Sell overbought
        for sym, pos in ctx.portfolio.positions.items():
            if hasattr(pos, "size") and pos.size > 0 and scores.get(sym, 0) > self.exit_threshold:
                signals.append(Signal.close(sym))

        # Buy oversold
        oversold = [(s, z) for s, z in scores.items() if z < self.entry_threshold]
        oversold.sort(key=lambda x: x[1])  # most oversold first
        selected = oversold[: self.top_k]

        weight = self.allocation if self.allocation > 0 else (1.0 / max(self.top_k, 1))
        for sym, _ in selected:
            if not ctx.portfolio.has_position(sym):
                signals.append(Signal.buy(sym, weight=weight))

        return signals
