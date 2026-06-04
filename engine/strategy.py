from dataclasses import dataclass, field
import uuid
from typing import Literal


@dataclass
class Signal:
    symbol: str
    side: Literal["buy", "sell", "close", "target"]
    weight: float | None = None
    qty: int | None = None
    order_type: str = "market"
    limit_price: float | None = None
    signal_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    score: float = 0.0
    rank: int = 0

    @classmethod
    def buy(cls, symbol: str, weight: float = 1.0, score: float = 0.0, rank: int = 0) -> "Signal":
        return cls(symbol=symbol, side="buy", weight=weight, score=score, rank=rank)

    @classmethod
    def sell(cls, symbol: str, weight: float | None = None) -> "Signal":
        return cls(symbol=symbol, side="sell", weight=weight)

    @classmethod
    def close(cls, symbol: str) -> "Signal":
        return cls(symbol=symbol, side="close")

    @classmethod
    def target(cls, symbol: str, weight: float) -> "Signal":
        return cls(symbol=symbol, side="target", weight=weight)


class Strategy:
    def __init__(self):
        self.risk_rules: list = []

    def parameters(self) -> dict:
        result = {}
        for cls in type(self).__mro__:
            for k in getattr(cls, "__annotations__", {}):
                if not k.startswith("_") and hasattr(self, k):
                    result[k] = getattr(self, k)
        return result

    def add_risk(self, rule):
        self.risk_rules.append(rule)

    def on_init(self, ctx):
        pass

    def on_bar(self, ctx, bar: int) -> list[Signal]:
        return []


class StrategyContext:
    def __init__(self, data, portfolio, config):
        self.data = data
        self.portfolio = portfolio
        self.config = config
        self._last_pred = None

    @property
    def universe(self) -> list[str]:
        return self.data.universe

    def _set_bar_data(self, bar_data):
        """Cache bar data for property access. Called by Engine each bar."""
        self._last_pred = bar_data.get("pred") if bar_data else None

    @property
    def predictions(self) -> dict | None:
        """Last bar's ML predictions: {symbol: score}. None if no ML model."""
        return self._last_pred
