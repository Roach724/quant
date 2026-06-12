"""TurtleTrading — Richard Dennis' legendary trend-following system.

Entry on Donchian channel breakout (N-day high), exit on M-day low.
Position sizing uses ATR-based volatility adjustment (risk 2% per trade).
"""
from __future__ import annotations

import logging
import numpy as np

from engine.strategy import Strategy, Signal

logger = logging.getLogger(__name__)


def _donchian(high: np.ndarray, low: np.ndarray, period: int) -> tuple[np.ndarray, np.ndarray]:
    """Compute Donchian channel: (upper, lower) for each bar."""
    n = len(high)
    upper = np.full(n, np.nan, dtype=float)
    lower = np.full(n, np.nan, dtype=float)
    if n < period:
        return upper, lower
    for i in range(period - 1, n):
        upper[i] = np.max(high[i - period + 1:i + 1])
        lower[i] = np.min(low[i - period + 1:i + 1])
    return upper, lower


def _atr(high: np.ndarray, low: np.ndarray, close: np.ndarray, period: int) -> np.ndarray:
    """Compute Average True Range (Wilder's smoothing)."""
    n = len(close)
    atr = np.full(n, np.nan, dtype=float)
    if n <= period:
        return atr
    tr = np.zeros(n, dtype=float)
    for i in range(1, n):
        tr[i] = max(
            high[i] - low[i],
            abs(high[i] - close[i - 1]),
            abs(low[i] - close[i - 1]),
        )
    atr[period] = np.mean(tr[1:period + 1])
    alpha = 1.0 / period
    for i in range(period + 1, n):
        atr[i] = alpha * tr[i] + (1 - alpha) * atr[i - 1]
    return atr


class TurtleTrading(Strategy):
    """Richard Dennis Turtle Trading System (1983).

    Parameters
    ----------
    entry_days : int
        Donchian entry breakout period (standard 20).
    exit_days : int
        Donchian exit breakdown period (standard 10).
    atr_period : int
        ATR lookback for position sizing (standard 20).
    risk_pct : float
        Risk per trade as fraction of equity (standard 0.02 = 2%).
    top_k : int
        Max concurrent positions.
    """

    entry_days: int = 20
    exit_days: int = 10
    atr_period: int = 20
    risk_pct: float = 0.02
    top_k: int = 5
    allocation: float = 0.0  # overridden by ATR-based sizing

    def __init__(self, **kwargs):
        super().__init__()
        for k, v in kwargs.items():
            if hasattr(self, k):
                setattr(self, k, v)

    def on_init(self, ctx):
        self._entry_bar: dict[str, int] = {}
        self._entry_price: dict[str, float] = {}

    def on_bar(self, ctx, bar: int) -> list[Signal]:
        min_bars = max(self.entry_days, self.atr_period) + 5
        if bar < min_bars:
            return []

        signals: list[Signal] = []
        donchian: dict[str, tuple[float, float, float]] = {}  # (upper, lower, atr)
        closes: dict[str, float] = {}

        for sym in ctx.universe:
            try:
                close_s = ctx.data.close[sym].iloc[: bar + 1]
                high_s = (ctx.data.high[sym].iloc[: bar + 1]
                          if hasattr(ctx.data, 'high') and ctx.data.high is not None
                          else close_s)
                low_s = (ctx.data.low[sym].iloc[: bar + 1]
                         if hasattr(ctx.data, 'low') and ctx.data.low is not None
                         else close_s)
                cp = close_s.dropna().values.astype(float)
                hp = high_s.dropna().values.astype(float)
                lp = low_s.dropna().values.astype(float)
                if len(cp) < min_bars:
                    continue
                closes[sym] = cp[-1]
                up_entry, _ = _donchian(hp, lp, self.entry_days)
                _, lo_exit = _donchian(hp, lp, self.exit_days)
                a = _atr(hp, lp, cp, self.atr_period)
                donchian[sym] = (
                    float(up_entry[-1]) if not np.isnan(up_entry[-1]) else np.inf,
                    float(lo_exit[-1]) if not np.isnan(lo_exit[-1]) else -np.inf,
                    float(a[-1]) if not np.isnan(a[-1]) else 0,
                )
            except (KeyError, IndexError, TypeError):
                continue

        # ── Exit: price breaks below exit Donchian (N-day low) ──
        for sym, pos in list(ctx.portfolio.positions.items()):
            if not (hasattr(pos, "size") and pos.size > 0):
                continue
            if sym not in donchian or sym not in closes:
                continue
            _, lo_exit, _ = donchian[sym]
            if closes[sym] <= lo_exit:
                signals.append(Signal.close(sym))
                self._entry_bar.pop(sym, None)
                self._entry_price.pop(sym, None)

        # ── Entry: price breaks above entry Donchian (N-day high) ──
        candidates: list[tuple[str, float, float]] = []  # (sym, strength, atr)
        for sym in donchian:
            if ctx.portfolio.has_position(sym):
                continue
            up_entry, _, atr_val = donchian[sym]
            price = closes.get(sym)
            if price is None or up_entry is np.inf or atr_val <= 0:
                continue
            # Check breakout (price >= upper Donchian)
            if price >= up_entry:
                # Strength = % above breakout
                strength = (price - up_entry) / up_entry
                candidates.append((sym, strength, atr_val))

        candidates.sort(key=lambda x: x[1], reverse=True)
        selected = candidates[: self.top_k]

        for sym, strength, atr_val in selected:
            # ATR-based position sizing: risk_pct of equity / ATR
            # Units = (equity × risk_pct) / ATR → weight = risk_pct / (ATR/price)
            price = closes[sym]
            if price > 0 and atr_val > 0:
                # Weight is proportional to risk_pct / (ATR/price) = risk_pct * price / ATR
                # but capped to avoid over-concentration
                turtle_weight = min(self.risk_pct * price / atr_val, 0.25)
            else:
                turtle_weight = 1.0 / max(self.top_k, 1)

            signals.append(Signal.buy(sym, weight=turtle_weight, score=float(strength)))
            self._entry_bar[sym] = bar
            self._entry_price[sym] = price

        return signals
