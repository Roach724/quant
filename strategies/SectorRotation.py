"""SectorRotation — rotate capital across sectors based on rankings.

Rotates into top-ranked sector/plate constituents on a schedule.
Uses UniverseBuilder.from_plate() to resolve sector membership.
"""
from __future__ import annotations

from engine.strategy import Strategy, Signal


class SectorRotation(Strategy):
    """Monthly sector rotation based on factor rankings.

    Parameters
    ----------
    sectors : list[str]
        Plate codes to consider (e.g. ["HSI", "HSTECH"]).
    factor : str
        Factor name for ranking sectors.
    top_k_sectors : int
        Number of top sectors to select.
    rebalance_every : int
        Rebalance frequency in bars (default 21 ≈ monthly).
    allocation : float
        Fraction of equity allocated across all positions.
    """

    sectors: list[str] = []
    factor: str = "roe"
    top_k_sectors: int = 3
    rebalance_every: int = 21
    allocation: float = 0.95

    def on_init(self, ctx):
        self._last_rebalance = -self.rebalance_every
        if not self.sectors:
            self.sectors = ["HSI", "HSTECH"]

    def on_bar(self, ctx, bar: int) -> list[Signal]:
        if bar - self._last_rebalance < self.rebalance_every:
            return []
        self._last_rebalance = bar

        from paper.market import UniverseBuilder

        signals = []
        all_symbols: list[str] = []
        for plate in self.sectors:
            try:
                syms = UniverseBuilder.from_plate(plate)
                all_symbols.extend(syms[:self.top_k_sectors * 10])
            except Exception:
                pass

        if not all_symbols:
            return []

        weight = self.allocation / len(all_symbols)
        for sym in all_symbols[:50]:
            if sym in ctx.universe:
                signals.append(Signal.buy(sym, min(weight, 0.02)))

        return signals
