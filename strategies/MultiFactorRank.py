"""MultiFactorRank — cross-sectional multi-factor ranking strategy.

Computes a set of factors for all symbols, cross-sectionally z-scores
each factor, and ranks by equal-weighted composite score.  Rebalances
on schedule, clearing old positions and entering new top-K.
"""
from __future__ import annotations

import logging
import numpy as np
import pandas as pd

from engine.strategy import Strategy, Signal

logger = logging.getLogger(__name__)

# Default factor pool with direction:
#   +1 = higher is better (momentum, quality)
#   -1 = lower is better (volatility, turnover)
_DEFAULT_FACTORS = {
    "ret_1d": +1,
    "ret_5d": +1,
    "ret_20d": +1,
    "vol_5d": -1,
    "vol_20d": -1,
    "turnover_5d": -1,
    "rsi_14": +1,
}


class MultiFactorRank(Strategy):
    """Cross-sectional multi-factor ranking strategy.

    Parameters
    ----------
    factor_weights : dict[str, int]
        Factor names → direction (+1 or -1).  Default uses 7 factors.
    top_k : int
        Max concurrent positions.
    rebalance_every : int
        Bars between rebalances.
    lookback : int
        OHLCV window for factor computation (default 100).
    """

    top_k: int = 10
    rebalance_every: int = 13
    lookback: int = 100
    allocation: float = 0.0

    def __init__(self, **kwargs):
        super().__init__()
        # Extract factor_weights before generic setattr (it's a dict)
        self.factor_weights: dict[str, int] = dict(_DEFAULT_FACTORS)
        for k, v in kwargs.items():
            if k == "factor_weights" and isinstance(v, dict):
                self.factor_weights = {fk: int(fv) for fk, fv in v.items()}
            elif hasattr(self, k):
                setattr(self, k, v)

    def on_init(self, ctx):
        self._last_rebalance = -self.rebalance_every
        self._factor_names = list(self.factor_weights.keys())

    def on_bar(self, ctx, bar: int) -> list[Signal]:
        if bar - self._last_rebalance < self.rebalance_every:
            return []
        if bar < max(self.lookback, 40):
            return []
        self._last_rebalance = bar

        signals: list[Signal] = []

        # ── Build OHLCV per symbol ──
        from factors.tech_builder import TechFactorBuilder

        start_idx = max(0, bar - self.lookback)
        symbol_ohlcv: dict[str, list[dict]] = {}
        for i in range(start_idx, bar + 1):
            for sym in ctx.universe:
                try:
                    close_val = ctx.data.close.iloc[i].get(sym, np.nan)
                    if pd.isna(close_val):
                        continue
                except (KeyError, IndexError):
                    continue
                open_val = close_val
                high_val = close_val
                low_val = close_val
                vol_val = 0
                try:
                    if hasattr(ctx.data, 'open') and ctx.data.open is not None:
                        open_val = ctx.data.open.iloc[i].get(sym, close_val)
                except Exception:
                    pass
                try:
                    if hasattr(ctx.data, 'high') and ctx.data.high is not None:
                        high_val = ctx.data.high.iloc[i].get(sym, close_val)
                except Exception:
                    pass
                try:
                    if hasattr(ctx.data, 'low') and ctx.data.low is not None:
                        low_val = ctx.data.low.iloc[i].get(sym, close_val)
                except Exception:
                    pass
                try:
                    if hasattr(ctx.data, 'volume') and ctx.data.volume is not None:
                        vol_val = int(ctx.data.volume.iloc[i].get(sym, 0))
                except Exception:
                    pass
                symbol_ohlcv.setdefault(sym, []).append({
                    "date": ctx.data.timestamp[i],
                    "open": open_val, "high": high_val,
                    "low": low_val, "close": close_val, "volume": vol_val,
                })

        # ── Compute factors → cross-sectional z-score → composite ──
        fb = TechFactorBuilder()
        factor_data: dict[str, dict[str, float]] = {fn: {} for fn in self._factor_names}
        for sym in ctx.universe:
            rows = symbol_ohlcv.get(sym, [])
            if len(rows) < 20:
                continue
            sym_df = pd.DataFrame(rows)
            try:
                factors = fb.compute(self._factor_names, sym_df)
            except Exception:
                continue
            if factors.empty:
                continue
            latest = factors.iloc[-1]
            for fn in self._factor_names:
                if fn in latest.index and not pd.isna(latest[fn]):
                    factor_data[fn][sym] = float(latest[fn])

        # Cross-sectional z-score per factor
        composite: dict[str, float] = {}
        for fn in self._factor_names:
            vals = factor_data[fn]
            if len(vals) < 3:
                continue
            syms = list(vals.keys())
            arr = np.array([vals[s] for s in syms], dtype=float)
            mu = np.mean(arr)
            std = np.std(arr)
            if std == 0:
                continue
            z_scores = (arr - mu) / std
            direction = self.factor_weights.get(fn, 1)
            for i, sym in enumerate(syms):
                composite[sym] = composite.get(sym, 0.0) + direction * float(z_scores[i])

        if not composite:
            return []

        # ── Rank & select top-K ──
        ranked = sorted(composite.items(), key=lambda x: x[1], reverse=True)
        top_symbols = {sym for sym, _ in ranked[: self.top_k]}

        # ── Exit: positions no longer in top-K ──
        for sym, pos in list(ctx.portfolio.positions.items()):
            if hasattr(pos, "size") and pos.size > 0 and sym not in top_symbols:
                signals.append(Signal.close(sym))

        # ── Entry: new top-K ──
        weight = self.allocation if self.allocation > 0 else (1.0 / max(self.top_k, 1))
        for i, (sym, score) in enumerate(ranked[: self.top_k]):
            if not ctx.portfolio.has_position(sym):
                signals.append(Signal.buy(sym, weight=weight, score=float(score), rank=i + 1))

        return signals
