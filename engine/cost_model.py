"""Unified transaction cost model — single source of truth for slippage & commission.

Supports market-aware defaults for US equities, HK equities, and crypto.
All runners (paper, live, multi-day) should use TransactionCost.from_config()
to avoid duplicated/brittle default values.
"""

from __future__ import annotations

from dataclasses import dataclass

# Market-aware defaults: (slippage_bps, commission_bps, min_commission)
_MARKET_DEFAULTS = {
    "us": (5.0, 1.0, 1.0),
    "hk": (10.0, 5.0, 3.0),
    "crypto": (3.0, 10.0, 0.0),
}


@dataclass
class TransactionCost:
    """Slippage and commission parameters for a trading run.

    Attributes
    ----------
    slippage_bps : float
        Expected slippage in basis points (1 bp = 0.01%).
    commission_bps : float
        Commission rate in basis points of notional.
    min_commission : float
        Minimum commission per trade (in base currency).
    """

    slippage_bps: float = 5.0
    commission_bps: float = 1.0
    min_commission: float = 1.0

    @classmethod
    def for_market(cls, market: str, **overrides) -> TransactionCost:
        """Create a TransactionCost with defaults for the given market.

        Parameters
        ----------
        market : str
            One of 'us', 'hk', 'crypto'.
        **overrides
            Optional overrides for slippage_bps / commission_bps / min_commission.
        """
        defaults = _MARKET_DEFAULTS.get(market, _MARKET_DEFAULTS["us"])
        return cls(
            slippage_bps=overrides.get("slippage_bps", defaults[0]),
            commission_bps=overrides.get("commission_bps", defaults[1]),
            min_commission=overrides.get("min_commission", defaults[2]),
        )

    @classmethod
    def from_config(cls, cfg: dict | None, market: str = "us") -> TransactionCost:
        """Create from a config dict (e.g. broker.paper or broker.live section).

        Falls back to market-aware defaults when keys are missing or None.
        """
        if cfg is None:
            cfg = {}
        overrides = {
            k: cfg[k]
            for k in ("slippage_bps", "commission_bps", "min_commission")
            if cfg.get(k) is not None
        }
        return cls.for_market(market, **overrides)
