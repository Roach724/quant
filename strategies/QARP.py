"""QARP — Quality At a Reasonable Price composite-score long-only strategy.

Computes a composite QARP score from BigQuery F10 data (valuation + quality)
and selects top-K symbols on a rebalance schedule.
"""
from __future__ import annotations

import logging
import numpy as np

from engine.strategy import Strategy, Signal

logger = logging.getLogger(__name__)


class QARP(Strategy):
    """Quality At a Reasonable Price — composite-score long-only strategy.

    On init, queries BigQuery for valuation (PE/PS/PB) and quality
    (Morningstar star_rating) data, then re-ranks on each rebalance.

    Parameters
    ----------
    top_k : int
        Number of symbols to hold.
    rebalance_every : int
        Rebalance frequency in bars (default 21 ≈ monthly).
    """

    top_k: int = 10
    rebalance_every: int = 21

    def __init__(self, **kwargs):
        super().__init__()
        for k, v in kwargs.items():
            if hasattr(self, k):
                setattr(self, k, v)

    def on_init(self, ctx):
        self._last_rebalance = -self.rebalance_every
        self._current_selection: set[str] = set()
        self._scores: dict[str, float] = {}
        self._scores_loaded = False

    def _load_scores(self, symbols: list[str]) -> dict[str, float]:
        """Load QARP scores from BigQuery, caching for the run."""
        if not self._scores_loaded:
            try:
                from factors.composite import compute_qarp_scores
                market = "us" if any(s.startswith("US.") for s in symbols) else "hk"
                self._scores = compute_qarp_scores(market, set(symbols))
                logger.info("QARP: loaded %d scores for %s", len(self._scores), market)
            except Exception:
                logger.warning("QARP: failed to load scores", exc_info=True)
                self._scores = {}
            self._scores_loaded = True
        return self._scores

    def on_bar(self, ctx, bar: int) -> list[Signal]:
        if bar - self._last_rebalance < self.rebalance_every:
            return []
        self._last_rebalance = bar

        symbols = list(ctx.universe)
        if not symbols:
            return []

        scores = self._load_scores(symbols)
        if not scores:
            return []

        # Filter to universe and rank
        ranked = [(s, v) for s, v in scores.items()
                  if s in ctx.universe and not np.isnan(v)]
        if not ranked:
            return []

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
