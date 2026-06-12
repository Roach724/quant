"""SectorRotation — rotate capital across sectors based on factor rankings.

Ranks sectors by aggregate factor score, then picks top stocks from
selected sectors.  Rebalances on schedule, clearing old positions.
"""
from __future__ import annotations

import logging
import numpy as np
import pandas as pd

from engine.strategy import Strategy, Signal

logger = logging.getLogger(__name__)

# Default sector plates per market (Futu plate codes)
_DEFAULT_PLATES = {
    "us": [
        "SP500",       # S&P 500 — broad market
        "NASDAQ100",   # Nasdaq 100 — tech
        "DJIA",        # Dow Jones — blue chips
        "RUSSELL2000", # Russell 2000 — small caps  
        "SOX",         # Philadelphia Semiconductor
        "XLY",         # Consumer Discretionary
        "XLF",         # Financials
        "XLE",         # Energy
        "XLK",         # Technology
        "XLV",         # Healthcare
    ],
    "hk": [
        "HSI",         # 恒生指数
        "HSTECH",      # 恒生科技
        "HSCEI",       # 国企指数
        "HSCCI",       # 红筹指数
        "HSPRO",       # 地产
        "HSFIN",       # 金融
    ],
}


class SectorRotation(Strategy):
    """Factor-driven sector rotation strategy.

    Parameters
    ----------
    sectors : list[str]
        Plate codes, e.g. ["HSI","HSTECH"]. Empty = market default.
    factor_id : str
        Factor to rank by (default "ret_20d" momentum).
    top_k_sectors : int
        Number of top sectors to select per rebalance.
    top_n_per_sector : int
        Max stocks per selected sector.
    rebalance_every : int
        Rebalance frequency in bars (default 21 ≈ monthly).
    """

    sectors: list[str] = []
    factor_id: str = "ret_20d"
    top_k_sectors: int = 3
    top_n_per_sector: int = 5
    rebalance_every: int = 21
    allocation: float = 0.0  # 0 = equal weight

    def __init__(self, **kwargs):
        super().__init__()
        for k, v in kwargs.items():
            if hasattr(self, k):
                setattr(self, k, v)

    def on_init(self, ctx):
        self._last_rebalance = -self.rebalance_every
        market = self._detect_market(ctx)
        if not self.sectors:
            self.sectors = _DEFAULT_PLATES.get(market, ["HSI", "HSTECH"])
        # Cache plate → symbols mapping (fetch once at init)
        self._plate_symbols: dict[str, list[str]] = {}
        self._load_plates(ctx)
        logger.info("SectorRotation: %d plates loaded for %s", len(self._plate_symbols), market)

    def _detect_market(self, ctx) -> str:
        for sym in getattr(ctx, 'universe', []):
            if sym.startswith("US."):
                return "us"
            if sym.startswith("HK."):
                return "hk"
        return "us"

    def _load_plates(self, ctx):
        """Resolve plate codes → symbol lists from Futu API (with caching)."""
        from paper.market import UniverseBuilder
        for plate in self.sectors:
            try:
                syms = UniverseBuilder.from_plate(plate)
                # Filter to symbols in universe
                valid = [s for s in syms if s in ctx.universe]
                if valid:
                    self._plate_symbols[plate] = valid
                    logger.debug("  %s: %d symbols", plate, len(valid))
            except Exception:
                logger.debug("  %s: unavailable (Futu not connected?)", plate)

    def on_bar(self, ctx, bar: int) -> list[Signal]:
        if bar - self._last_rebalance < self.rebalance_every:
            return []
        if bar < 20:
            return []
        self._last_rebalance = bar

        if not self._plate_symbols:
            return []

        # ── Compute factor score per symbol ──
        from factors.tech_builder import TechFactorBuilder
        fb = TechFactorBuilder()

        symbol_ohlcv = self._build_ohlcv(ctx, bar)
        factor_scores: dict[str, float] = {}

        for sym in ctx.universe:
            rows = symbol_ohlcv.get(sym, [])
            if len(rows) < 20:
                continue
            sym_df = pd.DataFrame(rows)
            try:
                factors = fb.compute([self.factor_id], sym_df)
            except Exception:
                continue
            if factors.empty or self.factor_id not in factors.columns:
                continue
            val = float(factors[self.factor_id].iloc[-1])
            if not np.isnan(val):
                factor_scores[sym] = val

        if not factor_scores:
            return []

        # ── Sector aggregation: mean factor score of constituents ──
        sector_scores: dict[str, float] = {}
        for plate, syms in self._plate_symbols.items():
            scores = [factor_scores.get(s) for s in syms if s in factor_scores]
            if len(scores) >= 3:
                sector_scores[plate] = float(np.mean(scores))

        if not sector_scores:
            return []

        # ── Select top sectors ──
        ranked_sectors = sorted(sector_scores.items(), key=lambda x: x[1], reverse=True)
        top_sectors = ranked_sectors[:self.top_k_sectors]

        # ── Select top stocks per sector ──
        selected: set[str] = set()
        for plate, _ in top_sectors:
            syms = self._plate_symbols.get(plate, [])
            ranked = sorted(
                [(s, factor_scores.get(s, -999)) for s in syms if s in factor_scores],
                key=lambda x: x[1], reverse=True,
            )
            for sym, _ in ranked[:self.top_n_per_sector]:
                selected.add(sym)

        signals: list[Signal] = []

        # ── Sell positions no longer selected ──
        for sym, pos in list(ctx.portfolio.positions.items()):
            if hasattr(pos, "size") and pos.size > 0 and sym not in selected:
                signals.append(Signal.close(sym))

        # ── Buy new selections ──
        n_buy = max(len(selected), 1)
        weight = self.allocation / n_buy if self.allocation > 0 else (1.0 / n_buy)
        for sym in selected:
            if not ctx.portfolio.has_position(sym):
                signals.append(Signal.buy(sym, weight=min(weight, 0.10)))

        return signals

    def _build_ohlcv(self, ctx, bar: int) -> dict[str, list[dict]]:
        """Build OHLCV dict from context data (window = last 100 bars)."""
        window = 100
        start_idx = max(0, bar - window)
        result: dict[str, list[dict]] = {}
        for i in range(start_idx, bar + 1):
            for sym in ctx.universe:
                try:
                    close_val = ctx.data.close.iloc[i].get(sym, np.nan)
                except Exception:
                    continue
                if pd.isna(close_val):
                    continue
                open_val = close_val
                high_val = close_val
                low_val = close_val
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
                result.setdefault(sym, []).append({
                    "date": getattr(ctx.data, 'timestamp', [None])[i] if hasattr(ctx.data, 'timestamp') else None,
                    "open": open_val, "high": high_val,
                    "low": low_val, "close": close_val, "volume": 0,
                })
        return result
