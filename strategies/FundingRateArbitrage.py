"""FundingRateArbitrage — cross-sectional funding-rate carry trade.

Short high-funding coins, long low-funding coins via cross-sectional z-score.
"""
from __future__ import annotations

import numpy as np

from engine.strategy import Strategy, Signal


class FundingRateArbitrage(Strategy):
    """Short high-funding coins, long low-funding coins via cross-sectional z-score.

    Reads funding_rate from ctx.predictions (DataFrameSource pred=...).
    Requires a data source that provides funding_rate as the 'pred' column.

    Parameters
    ----------
    entry_z : float
        Z-score threshold to enter (abs > this value triggers).
    exit_z : float
        Z-score threshold to exit (abs < this value closes).
    top_k : int
        Max positions on each side (long + short).
    lookback : int
        Bars to compute z-score rolling stats.
    """

    entry_z: float = 1.5
    exit_z: float = 0.3
    top_k: int = 3
    lookback: int = 20

    def on_init(self, ctx):
        self._long_entries: dict[str, float] = {}
        self._short_entries: dict[str, float] = {}

    def on_bar(self, ctx, bar: int) -> list[Signal]:
        if bar < self.lookback:
            return []

        if ctx.predictions is None:
            return []
        fr = ctx.predictions

        fr_values = [(s, v) for s, v in fr.items()
                     if s in ctx.universe and not np.isnan(v)]
        if len(fr_values) < 3:
            return []

        syms, vals = zip(*fr_values)
        mu = np.mean(vals)
        sigma = np.std(vals)
        if sigma == 0:
            return []

        z_scores = {s: (v - mu) / sigma for s, v in fr_values}

        extreme_short = [(s, z) for s, z in z_scores.items() if z > self.entry_z]
        extreme_long = [(s, z) for s, z in z_scores.items() if z < -self.entry_z]
        extreme_short.sort(key=lambda x: x[1], reverse=True)
        extreme_long.sort(key=lambda x: x[1])

        signals: list[Signal] = []

        for sym in list(self._long_entries):
            if abs(z_scores.get(sym, 0)) < self.exit_z:
                signals.append(Signal.close(sym))
                del self._long_entries[sym]

        for sym in list(self._short_entries):
            if abs(z_scores.get(sym, 0)) < self.exit_z:
                signals.append(Signal.close(sym))
                del self._short_entries[sym]

        for sym, _ in extreme_long[:self.top_k]:
            if sym not in self._long_entries and sym not in self._short_entries:
                signals.append(Signal.buy(sym, weight=1.0 / self.top_k))
                self._long_entries[sym] = float(ctx.data.close.iloc[bar][sym])

        for sym, _ in extreme_short[:self.top_k]:
            if sym not in self._short_entries and sym not in self._long_entries:
                signals.append(Signal.sell(sym, weight=1.0 / self.top_k))
                self._short_entries[sym] = float(ctx.data.close.iloc[bar][sym])

        return signals
