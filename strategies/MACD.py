"""MACD — Moving Average Convergence Divergence trend-following strategy.

Classic Gerald Appel indicator (1970s).  Buys on golden cross (MACD
crosses above signal line), closes on death cross.
"""
from __future__ import annotations

import logging
import numpy as np

from engine.strategy import Strategy, Signal

logger = logging.getLogger(__name__)


def _ema(series: np.ndarray, period: int) -> np.ndarray:
    """Compute Exponential Moving Average for a 1-D series.

    Uses the standard alpha = 2 / (period + 1) weighting.
    Skips NaN in seed computation and carries forward through NaN gaps.
    """
    if len(series) < period:
        return np.full_like(series, np.nan, dtype=float)
    alpha = 2.0 / (period + 1)
    result = np.full_like(series, np.nan, dtype=float)
    # Find the first stretch of 'period' consecutive non-NaN values for seeding
    seed_start = -1
    consecutive = 0
    for i in range(len(series)):
        if not np.isnan(series[i]):
            consecutive += 1
            if consecutive >= period:
                seed_start = i - period + 1
                break
        else:
            consecutive = 0
    if seed_start < 0:
        return result  # not enough valid data
    # Seed EMA at period-th valid point
    seed_idx = seed_start + period - 1
    result[seed_idx] = np.mean(series[seed_start:seed_start + period])
    for i in range(seed_idx + 1, len(series)):
        if np.isnan(series[i]):
            result[i] = result[i - 1]  # carry forward through NaN gaps
        else:
            result[i] = alpha * series[i] + (1 - alpha) * result[i - 1]
    return result


class MACD(Strategy):
    """MACD golden-cross / death-cross trend-following strategy.

    Parameters
    ----------
    fast : int
        Fast EMA period (default 12).
    slow : int
        Slow EMA period (default 26).
    signal_period : int
        Signal line EMA period (default 9).
    top_k : int
        Max concurrent positions.
    """

    fast: int = 12
    slow: int = 26
    signal_period: int = 9
    top_k: int = 5
    allocation: float = 0.0

    def __init__(self, **kwargs):
        super().__init__()
        for k, v in kwargs.items():
            if hasattr(self, k):
                setattr(self, k, v)

    def on_init(self, ctx):
        self._prev_macd: dict[str, float] = {}
        self._prev_signal: dict[str, float] = {}

    def on_bar(self, ctx, bar: int) -> list[Signal]:
        min_bars = max(self.slow + self.signal_period, 40)
        if bar < min_bars:
            return []

        signals: list[Signal] = []

        # ── Compute MACD for every symbol ──
        macd: dict[str, float] = {}
        sig_line: dict[str, float] = {}

        for sym in ctx.universe:
            try:
                close_series = ctx.data.close[sym].iloc[: bar + 1]
                if len(close_series.dropna()) < min_bars:
                    continue
                prices = close_series.values.astype(float)
                ema_fast = _ema(prices, self.fast)
                ema_slow = _ema(prices, self.slow)
                macd_series = ema_fast - ema_slow
                sig_series = _ema(macd_series, self.signal_period)
                if np.isnan(macd_series[-1]) or np.isnan(sig_series[-1]):
                    continue
                macd[sym] = float(macd_series[-1])
                sig_line[sym] = float(sig_series[-1])
            except (KeyError, IndexError, TypeError):
                continue

        if not macd:
            return []

        # ── Exit: death cross (MACD crosses below signal) ──
        for sym, pos in list(ctx.portfolio.positions.items()):
            if not (hasattr(pos, "size") and pos.size > 0):
                continue
            prev_m = self._prev_macd.get(sym)
            prev_s = self._prev_signal.get(sym)
            curr_m = macd.get(sym)
            curr_s = sig_line.get(sym)
            if None in (prev_m, prev_s, curr_m, curr_s):
                continue
            # Death cross: MACD was above, now below
            if prev_m > prev_s and curr_m < curr_s:
                signals.append(Signal.close(sym))

        # ── Entry: golden cross (MACD crosses above signal) ──
        crosses: list[tuple[str, float]] = []
        for sym in macd:
            if ctx.portfolio.has_position(sym):
                continue
            prev_m = self._prev_macd.get(sym)
            prev_s = self._prev_signal.get(sym)
            curr_m = macd[sym]
            curr_s = sig_line[sym]
            if None in (prev_m, prev_s):
                continue
            # Golden cross: MACD was below, now above
            if prev_m < prev_s and curr_m > curr_s:
                # Momentum score = MACD distance from signal (strength of cross)
                crosses.append((sym, curr_m - curr_s))

        # Rank by strongest cross
        crosses.sort(key=lambda x: x[1], reverse=True)
        selected = crosses[: self.top_k]

        weight = self.allocation if self.allocation > 0 else (1.0 / max(self.top_k, 1))
        for sym, strength in selected:
            signals.append(Signal.buy(sym, weight=weight, score=float(strength)))

        # ── Store for next bar's cross detection ──
        self._prev_macd = macd
        self._prev_signal = sig_line

        return signals
