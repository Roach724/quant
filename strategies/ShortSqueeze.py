"""ShortSqueeze — bet on upward pressure from short covering.

Reads model-generated scores from ctx.predictions and selects top-ranked
symbols to bet on short squeeze candidates.
"""
from __future__ import annotations

from engine.strategy import Strategy, Signal


class ShortSqueeze(Strategy):
    """Short squeeze: high short interest + low days-to-cover + upward momentum.

    Parameters
    ----------
    top_k : int
        Number of symbols to hold.
    rebalance_every : int
        Rebalance frequency in bars.
    allocation : float
        Fraction of equity allocated across all positions.
    """

    top_k: int = 10
    rebalance_every: int = 5
    allocation: float = 0.95

    def on_init(self, ctx):
        self._last_rebalance = -self.rebalance_every

    def on_bar(self, ctx, bar: int) -> list[Signal]:
        if bar - self._last_rebalance < self.rebalance_every:
            return []
        self._last_rebalance = bar

        if ctx.predictions is None:
            return []

        scores = {s: v for s, v in ctx.predictions.items()
                  if s in ctx.universe and not (isinstance(v, float) and (v != v))}
        if len(scores) < 3:
            return []

        sorted_symbols = sorted(scores, key=scores.get, reverse=True)
        selected = sorted_symbols[:self.top_k]
        weight = min(self.allocation / len(selected), 0.10)

        signals = []
        for sym in selected:
            if ctx.portfolio.positions.get(sym):
                signals.append(Signal.target(sym, weight))
            else:
                signals.append(Signal.buy(sym, weight))

        for sym in list(ctx.portfolio.positions.keys()):
            if sym not in selected:
                signals.append(Signal.close(sym))

        return signals
