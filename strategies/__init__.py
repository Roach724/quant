"""Quant strategies — built-in strategy classes for live/paper trading.

All strategies subclass `engine.strategy.Strategy`.
Individual strategy files can be edited via the Admin UI.
"""

from strategies.BuyHold import BuyHold
from strategies.FundingRateArbitrage import FundingRateArbitrage
from strategies.MeanReversion import MeanReversion
from strategies.QARP import QARP
from strategies.SectorRotation import SectorRotation
from strategies.ShortSqueeze import ShortSqueeze
from strategies.SimpleMomentum import SimpleMomentum
from strategies.MLPrediction import MLPrediction

__all__ = [
    "BuyHold",
    "FundingRateArbitrage",
    "MeanReversion",
    "MLPrediction",
    "QARP",
    "SectorRotation",
    "ShortSqueeze",
    "SimpleMomentum",
    "list_strategies",
    "get_strategy",
]

# ── Registry ─────────────────────────────────────────────────────────

_BUILTIN_STRATEGIES: dict[str, type] = {
    "BuyHold": BuyHold,
    "SimpleMomentum": SimpleMomentum,
    "MeanReversion": MeanReversion,
    "MLPrediction": MLPrediction,
    "FundingRateArbitrage": FundingRateArbitrage,
    "QARP": QARP,
    "ShortSqueeze": ShortSqueeze,
    "SectorRotation": SectorRotation,
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
            "module": "strategies",
            "qualified": f"strategies.{name}",
            "doc": (cls.__doc__ or "").strip().split("\n")[0],
            "parameters": params,
        })
    return result


def get_strategy(qualified_name: str) -> type:
    """Resolve a qualified strategy class name and return the type.

    Supports: 'BuyHold', 'SimpleMomentum', 'strategies.BuyHold', etc.
    """
    class_name = qualified_name.split(".")[-1] if "." in qualified_name else qualified_name
    cls = _BUILTIN_STRATEGIES.get(class_name)
    if cls is None:
        raise ValueError(
            f"Unknown strategy {class_name!r}. Available: {list(_BUILTIN_STRATEGIES.keys())}"
        )
    return cls
