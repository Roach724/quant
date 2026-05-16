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

    @classmethod
    def buy(cls, symbol: str, weight: float = 1.0) -> "Signal":
        return cls(symbol=symbol, side="buy", weight=weight)

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

    @property
    def universe(self) -> list[str]:
        return self.data.universe
