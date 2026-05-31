"""Built-in example strategies for the paper runner.

All strategies subclass `engine.strategy.Strategy` and are parameterised
via class-level annotations so that CLI arguments can override defaults.
"""

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


class BuyHold(Strategy):
    """Buy every symbol in the universe on the first bar and hold until the end.

    Parameters
    ----------
    weight_per_symbol : float
        Fraction of portfolio equity allocated to each symbol (0–1).
    """

    weight_per_symbol: float = 0.1

    def on_bar(self, ctx, bar: int) -> list[Signal]:
        if bar != 0:
            return []
        # Equal-weight buy on the very first bar
        return [
            Signal.buy(sym, weight=self.weight_per_symbol)
            for sym in ctx.universe
        ]


class SimpleMomentum(Strategy):
    """Buy the top-K symbols by recent N-bar return.  Rebalance every R bars.

    Parameters
    ----------
    lookback : int
        Number of bars to compute momentum over.
    top_k : int
        How many symbols to hold at a time.
    rebalance_every : int
        Rebalance frequency in bars.
    allocation : float
        Fraction of equity allocated to each position (1.0 / top_k if 0).
    """

    lookback: int = 20
    top_k: int = 5
    rebalance_every: int = 5
    allocation: float = 0.0

    def on_init(self, ctx):
        self._last_rebalance = -self.rebalance_every  # trigger first bar

    def on_bar(self, ctx, bar: int) -> list[Signal]:
        if bar - self._last_rebalance < self.rebalance_every:
            return []

        if bar < self.lookback:
            return []

        self._last_rebalance = bar

        # Compute momentum score = (price_t / price_{t-N} - 1)
        scores = {}
        for sym in ctx.universe:
            try:
                p_now = ctx.data.close.iloc[bar][sym]
                p_prev = ctx.data.close.iloc[bar - self.lookback][sym]
                if p_prev and p_prev > 0:
                    scores[sym] = float(p_now / p_prev - 1.0)
            except (KeyError, IndexError):
                continue

        # Top-K by score
        ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        selected = {sym for sym, _ in ranked[: self.top_k]}

        signals: list[Signal] = []

        # Exit positions no longer selected
        for sym, pos in ctx.portfolio.positions.items():
            if hasattr(pos, "size") and pos.size > 0 and sym not in selected:
                signals.append(Signal.close(sym))

        # Enter new positions
        weight = self.allocation if self.allocation > 0 else (1.0 / max(self.top_k, 1))
        for sym in selected:
            if not ctx.portfolio.has_position(sym):
                signals.append(Signal.buy(sym, weight=weight))

        return signals


class MeanReversion(Strategy):
    """Buy top oversold symbols and sell overbought based on RSI-style logic.

    Parameters
    ----------
    lookback : int
        Period for mean / std calculation.
    entry_threshold : float
        Z-score *below* which to buy (e.g. –1.5).
    exit_threshold : float
        Z-score *above* which to sell (e.g. +1.5).
    top_k : int
        Max positions.
    """

    lookback: int = 30
    entry_threshold: float = -1.5
    exit_threshold: float = 1.5
    top_k: int = 5
    allocation: float = 0.0

    def on_bar(self, ctx, bar: int) -> list[Signal]:
        if bar < self.lookback:
            return []

        scores = {}
        for sym in ctx.universe:
            try:
                series = ctx.data.close[sym].iloc[bar - self.lookback : bar + 1]
                mu = float(series.mean())
                sigma = float(series.std())
                if sigma == 0:
                    continue
                z = float((series.iloc[-1] - mu) / sigma)
                scores[sym] = z
            except (KeyError, IndexError):
                continue

        signals: list[Signal] = []

        # Sell overbought
        for sym, pos in ctx.portfolio.positions.items():
            if hasattr(pos, "size") and pos.size > 0 and scores.get(sym, 0) > self.exit_threshold:
                signals.append(Signal.close(sym))

        # Buy oversold
        oversold = [(s, z) for s, z in scores.items() if z < self.entry_threshold]
        oversold.sort(key=lambda x: x[1])  # most oversold first
        selected = oversold[: self.top_k]

        weight = self.allocation if self.allocation > 0 else (1.0 / max(self.top_k, 1))
        for sym, _ in selected:
            if not ctx.portfolio.has_position(sym):
                signals.append(Signal.buy(sym, weight=weight))

        return signals


class QARP(Strategy):
    """Quality At a Reasonable Price — composite-score long-only strategy.

    Reads a composite score from ctx.predictions (dict of symbol → float),
    selects the top_k symbols, and rebalances monthly (~21 trading days).

    Parameters
    ----------
    top_k : int
        Number of symbols to hold.
    rebalance_every : int
        Rebalance frequency in bars (default 21 ≈ monthly).
    """

    top_k: int = 10
    rebalance_every: int = 21

    def on_init(self, ctx):
        self._last_rebalance = -self.rebalance_every
        self._current_selection: set[str] = set()

    def on_bar(self, ctx, bar: int) -> list[Signal]:
        if bar - self._last_rebalance < self.rebalance_every:
            return []

        self._last_rebalance = bar

        if ctx.predictions is None:
            return []

        pred = ctx.predictions

        # Filter to universe and remove NaN
        ranked = [(s, v) for s, v in pred.items()
                  if s in ctx.universe and not np.isnan(v)]
        if not ranked:
            return []

        # Sort by composite score descending, select top-k
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


# Registry for --list-strategies
_BUILTIN_STRATEGIES: dict[str, type] = {
    "BuyHold": BuyHold,
    "SimpleMomentum": SimpleMomentum,
    "MeanReversion": MeanReversion,
    "FundingRateArbitrage": FundingRateArbitrage,
    "QARP": QARP,
}


def list_strategies() -> list[dict]:
    """Return metadata for every built-in strategy."""
    result = []
    for name, cls in _BUILTIN_STRATEGIES.items():
        params = {}
        for k in cls.__annotations__:
            v = getattr(cls, k, None)
            if not k.startswith("_"):
                params[k] = v
        result.append({
            "name": name,
            "module": "paper.strategies",
            "qualified": f"paper.strategies.{name}",
            "doc": (cls.__doc__ or "").strip().split("\n")[0],
            "parameters": params,
        })
    return result


def get_strategy(qualified_name: str) -> type:
    """Resolve a qualified strategy class name and return the type.

    Supports: 'BuyHold', 'SimpleMomentum', 'paper.strategies.BuyHold', etc.
    """
    # Strip module prefix if present
    class_name = qualified_name.split(".")[-1] if "." in qualified_name else qualified_name
    cls = _BUILTIN_STRATEGIES.get(class_name)
    if cls is None:
        raise ValueError(
            f"Unknown strategy {class_name!r}. Available: {list(_BUILTIN_STRATEGIES.keys())}"
        )
    return cls
