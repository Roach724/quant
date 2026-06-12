"""BollingerBands — statistical mean-reversion strategy.

Buys when price touches the lower band (2σ below MA), closes when it
returns to the middle band or touches the upper band.
"""
from __future__ import annotations

import logging
import numpy as np

from engine.strategy import Strategy, Signal

logger = logging.getLogger(__name__)


class BollingerBands(Strategy):
    """Bollinger Bands mean-reversion strategy (John Bollinger, 1980s).

    Parameters
    ----------
    window : int
        MA and std-dev lookback period (default 20).
    sigma : float
        Number of standard deviations for bands (default 2.0).
    top_k : int
        Max concurrent positions.
    max_hold_bars : int
        Force-close after this many bars (default 60).
    """

    window: int = 20
    sigma: float = 2.0
    top_k: int = 5
    allocation: float = 0.0
    max_hold_bars: int = 60

    def __init__(self, **kwargs):
        super().__init__()
        for k, v in kwargs.items():
            if hasattr(self, k):
                setattr(self, k, v)

    def on_init(self, ctx):
        self._entry_bar: dict[str, int] = {}

    def on_bar(self, ctx, bar: int) -> list[Signal]:
        if bar < self.window:
            return []

        signals: list[Signal] = []
        bands: dict[str, tuple[float, float, float]] = {}  # sym → (lower, mid, upper)

        for sym in ctx.universe:
            try:
                series = ctx.data.close[sym].iloc[: bar + 1]
                prices = series.dropna().values.astype(float)
                if len(prices) < self.window:
                    continue
                window_prices = prices[-self.window:]
                mu = float(np.mean(window_prices))
                std = float(np.std(window_prices))
                if std == 0:
                    continue
                bands[sym] = (
                    mu - self.sigma * std,  # lower
                    mu,                     # mid
                    mu + self.sigma * std,  # upper
                )
            except (KeyError, IndexError, TypeError):
                continue

        current_prices = {}
        for sym in bands:
            try:
                current_prices[sym] = float(ctx.data.close[sym].iloc[bar])
            except (KeyError, IndexError, TypeError):
                continue

        # ── Exit: price returns to mid-band OR touches upper band ──
        for sym, pos in list(ctx.portfolio.positions.items()):
            if not (hasattr(pos, "size") and pos.size > 0):
                continue
            if sym not in bands or sym not in current_prices:
                continue
            lower, mid, upper = bands[sym]
            price = current_prices[sym]
            # Exit 1: price returns above mid (mean-reverted)
            if price >= mid:
                signals.append(Signal.close(sym))
                self._entry_bar.pop(sym, None)
            # Exit 2: timeout
            elif sym in self._entry_bar and bar - self._entry_bar[sym] > self.max_hold_bars:
                signals.append(Signal.close(sym))
                del self._entry_bar[sym]

        # ── Entry: price at or below lower band ──
        candidates: list[tuple[str, float]] = []
        for sym in bands:
            if ctx.portfolio.has_position(sym):
                continue
            lower, mid, upper = bands[sym]
            price = current_prices.get(sym)
            if price is None:
                continue
            if price <= lower:
                # Score = how far below lower band (more extreme = stronger signal)
                score = (lower - price) / lower if lower > 0 else 0
                candidates.append((sym, score))

        candidates.sort(key=lambda x: x[1], reverse=True)
        selected = candidates[: self.top_k]

        weight = self.allocation if self.allocation > 0 else (1.0 / max(self.top_k, 1))
        for sym, score in selected:
            signals.append(Signal.buy(sym, weight=weight, score=float(score)))
            self._entry_bar[sym] = bar

        return signals
