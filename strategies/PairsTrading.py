"""PairsTrading — market-neutral statistical arbitrage via pair spread z-score.

Takes a pre-defined list of cointegrated pairs and trades the spread
mean-reversion: long the underperformer + short the outperformer when
the spread is extreme, close both when it reverts.
"""
from __future__ import annotations

import logging
import numpy as np

from engine.strategy import Strategy, Signal

logger = logging.getLogger(__name__)


class PairsTrading(Strategy):
    """Statistical arbitrage via cointegrated pair trading.

    Parameters
    ----------
    pairs : list[list[str]]
        List of symbol pairs to trade, e.g. [["0700", "9988"], ["AAPL", "MSFT"]].
    lookback : int
        Window for spread mean/std calculation (default 60).
    entry_z : float
        Z-score threshold to enter (abs > entry_z triggers, default 2.0).
    exit_z : float
        Z-score threshold to exit (abs < exit_z closes, default 0.5).
    max_pairs : int
        Max concurrent pairs open (default 5).
    """

    pairs: list[list[str]] = []
    lookback: int = 60
    entry_z: float = 2.0
    exit_z: float = 0.5
    max_pairs: int = 5

    def __init__(self, **kwargs):
        super().__init__()
        for k, v in kwargs.items():
            if hasattr(self, k):
                setattr(self, k, v)

    def on_init(self, ctx):
        # Track open pairs: pair_key → entry_z_score
        self._open_pairs: dict[str, float] = {}

    def _pair_key(self, sym_a: str, sym_b: str) -> str:
        return f"{sym_a}|{sym_b}"

    def _compute_spread_z(
        self, sym_a: str, sym_b: str, bar: int, ctx,
    ) -> tuple[float, float, float] | None:
        """Compute spread z-score for a pair.

        Returns (z_score, price_a, price_b) or None if insufficient data.
        """
        try:
            pa = ctx.data.close[sym_a].iloc[bar - self.lookback: bar + 1]
            pb = ctx.data.close[sym_b].iloc[bar - self.lookback: bar + 1]
        except (KeyError, IndexError, TypeError):
            return None
        pa_vals = pa.dropna().values.astype(float)
        pb_vals = pb.dropna().values.astype(float)
        # Align lengths
        min_len = min(len(pa_vals), len(pb_vals))
        if min_len < self.lookback:
            return None
        pa_a = pa_vals[-min_len:]
        pb_a = pb_vals[-min_len:]
        # Spread: log-ratio
        spread = np.log(pa_a) - np.log(pb_a)
        mu = float(np.mean(spread))
        sigma = float(np.std(spread))
        if sigma == 0:
            return None
        z = float((spread[-1] - mu) / sigma)
        return z, pa_a[-1], pb_a[-1]

    def on_bar(self, ctx, bar: int) -> list[Signal]:
        if bar < self.lookback:
            return []
        if not self.pairs:
            return []

        signals: list[Signal] = []

        # ── Exit: spread mean-reverted → close both legs ──
        for pair_key in list(self._open_pairs):
            sym_a, sym_b = pair_key.split("|")
            result = self._compute_spread_z(sym_a, sym_b, bar, ctx)
            if result is None:
                continue
            z, _, _ = result
            if abs(z) < self.exit_z:
                # Close both legs (regardless of long/short)
                if ctx.portfolio.has_position(sym_a):
                    signals.append(Signal.close(sym_a))
                if ctx.portfolio.has_position(sym_b):
                    signals.append(Signal.close(sym_b))
                del self._open_pairs[pair_key]

        # ── Entry: extreme spread → open pairs ──
        if len(self._open_pairs) < self.max_pairs:
            candidates = []
            for sym_a, sym_b in self.pairs:
                pair_key = self._pair_key(sym_a, sym_b)
                if pair_key in self._open_pairs:
                    continue
                result = self._compute_spread_z(sym_a, sym_b, bar, ctx)
                if result is None:
                    continue
                z, price_a, price_b = result
                if abs(z) > self.entry_z:
                    direction = 1 if z < 0 else -1  # z<0: A underperforms → long A short B
                    candidates.append((pair_key, sym_a, sym_b, abs(z), direction, price_a, price_b))

            # Prioritize most extreme z-scores
            candidates.sort(key=lambda x: x[3], reverse=True)
            slots = self.max_pairs - len(self._open_pairs)
            for pair_key, sym_a, sym_b, z_abs, direction, _, _ in candidates[:slots]:
                # Long the underperformer, short the overperformer
                weight = 0.5  # 50% per leg
                if direction > 0:
                    # z < 0: A is cheap relative to B → long A, short B
                    signals.append(Signal.buy(sym_a, weight=weight))
                    signals.append(Signal.sell(sym_b, weight=weight))
                else:
                    # z > 0: A is expensive relative to B → short A, long B
                    signals.append(Signal.sell(sym_a, weight=weight))
                    signals.append(Signal.buy(sym_b, weight=weight))
                self._open_pairs[pair_key] = z_abs * direction

        return signals
