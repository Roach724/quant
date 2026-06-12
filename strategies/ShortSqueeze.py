"""ShortSqueeze — bet on upward pressure from short covering.

Queries BigQuery for HK daily short volume data + factor momentum,
computes a composite squeeze score, and selects top candidates.
"""
from __future__ import annotations

import logging

from engine.strategy import Strategy, Signal

logger = logging.getLogger(__name__)


class ShortSqueeze(Strategy):
    """Short squeeze: high short ratio + upward momentum.

    On init, queries BigQuery for short volume + momentum data.
    Re-ranks on each rebalance.

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

    def __init__(self, **kwargs):
        super().__init__()
        for k, v in kwargs.items():
            if hasattr(self, k):
                setattr(self, k, v)

    def on_init(self, ctx):
        self._last_rebalance = -self.rebalance_every
        self._scores: dict[str, float] = {}
        self._scores_loaded = False

    def _load_scores(self, symbols: list[str]) -> dict[str, float]:
        if not self._scores_loaded:
            try:
                from factors.composite import compute_short_squeeze_scores
                # Detect market: HK = bare numeric (00005, 00700), US = alphabetic (AAPL)
                # Paper runner normalizes HK symbols by stripping "HK." prefix
                market = "hk" if any(s and s[:1].isdigit() for s in symbols) else "us"
                self._scores = compute_short_squeeze_scores(market, set(symbols))
                logger.info("ShortSqueeze: loaded %d scores for %s", len(self._scores), market)
            except Exception:
                logger.warning("ShortSqueeze: failed to load scores", exc_info=True)
                self._scores = {}
            self._scores_loaded = True
        return self._scores

    def on_bar(self, ctx, bar: int) -> list[Signal]:
        if bar - self._last_rebalance < self.rebalance_every:
            return []
        self._last_rebalance = bar

        symbols = list(ctx.universe)
        scores = self._load_scores(symbols)
        if len(scores) < 3:
            return []

        # Filter to universe
        valid = {s: v for s, v in scores.items() if s in ctx.universe}
        if not valid:
            return []

        sorted_symbols = sorted(valid, key=valid.get, reverse=True)
        selected = sorted_symbols[:self.top_k]
        weight = min(self.allocation / max(len(selected), 1), 0.10)

        signals: list[Signal] = []
        for sym in selected:
            if ctx.portfolio.positions.get(sym):
                signals.append(Signal.target(sym, weight))
            else:
                signals.append(Signal.buy(sym, weight))

        for sym in list(ctx.portfolio.positions.keys()):
            if sym not in selected:
                signals.append(Signal.close(sym))

        return signals
