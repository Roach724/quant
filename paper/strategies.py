"""Built-in example strategies for the paper runner.

All strategies subclass `engine.strategy.Strategy` and are parameterised
via class-level annotations so that CLI arguments can override defaults.
"""

from engine.strategy import Strategy, Signal


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


# Registry for --list-strategies
_BUILTIN_STRATEGIES: dict[str, type] = {
    "BuyHold": BuyHold,
    "SimpleMomentum": SimpleMomentum,
    "MeanReversion": MeanReversion,
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
