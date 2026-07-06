"""Unified transaction cost model — single source of truth for slippage & commission.

Supports market-aware defaults for US equities, HK equities, and crypto.
All runners (paper, live, multi-day) should use TransactionCost.from_config()
to avoid duplicated/brittle default values.

Real broker fee schedules (BrokerFeeSchedule) model the actual cost structure per market:
- US: per-share commission + platform + clearing + SEC (sell) + TAF (sell) + CAT
- HK: percentage commission + flat platform + clearing + stamp (non-ETF) + trading + SFC + FRC
"""

from __future__ import annotations

from dataclasses import dataclass


class BrokerFeeSchedule:
    """Real-world broker fee schedules for US and HK equity markets.

    Fees modeled on Futu/moomoo standard pricing.
    All amounts in base currency (USD for US, HKD for HK).
    """

    @staticmethod
    def us_stock(qty: int, notional: float, side: str = "buy") -> float:
        """US stock total fees (Futu standard).

        Parameters
        ----------
        qty : int
            Number of shares.
        notional : float
            Trade amount (qty × price) in USD.
        side : str
            "buy" or "sell". SEC fee and TAF are sell-only.

        Returns
        -------
        float
            Total fees in USD.
        """
        commission = max(0.99, min(0.0049 * qty, 0.005 * notional))
        platform = max(1.0, min(0.005 * qty, 0.005 * notional))
        clearing = 0.003 * qty
        cat = 0.000003 * qty  # Consolidated Audit Trail (NMS stocks)

        total = commission + platform + clearing + cat

        if side == "sell":
            sec_fee = max(0.01, 0.0000206 * notional)
            taf = min(9.79, max(0.01, 0.000195 * qty))
            total += sec_fee + taf

        return round(total, 6)

    @staticmethod
    def hk_stock(qty: int, notional: float, side: str = "buy", *, is_etf: bool = False) -> float:
        """HK stock total fees (Futu standard).

        Parameters
        ----------
        qty : int
            Number of shares.
        notional : float
            Trade amount (qty × price) in HKD.
        side : str
            "buy" or "sell" (same fee structure for HK).
        is_etf : bool
            If True, stamp duty is waived (HKD 0.1% exemption for ETFs/warrants).

        Returns
        -------
        float
            Total fees in HKD.
        """
        commission = max(3.0, 0.0003 * notional)  # 0.03%, min HK$3
        platform = 15.0  # flat per order
        clearing = 0.000042 * notional  # 0.0042%
        trading_fee = max(0.01, 0.0000565 * notional)  # 0.00565%, min HK$0.01
        sfc = max(0.01, 0.000027 * notional)  # 0.0027%, min HK$0.01
        frc = 0.0000015 * notional  # 0.00015%

        total = commission + platform + clearing + trading_fee + sfc + frc

        if not is_etf:
            stamp = max(1.0, 0.001 * notional)  # 0.1%, min HK$1
            total += stamp

        return round(total, 6)


# Legacy market-aware defaults for simplified model (used when broker_fee is disabled)
_MARKET_DEFAULTS = {
    "us": (5.0, 1.0, 1.0),
    "hk": (10.0, 5.0, 3.0),
    "crypto": (3.0, 10.0, 0.0),
}

# Auto-enable real broker fees by market
_MARKET_BROKER_FEE = {
    "us": "us_stock",
    "hk": "hk_stock",
    # crypto: no real fee model, stays simplified
}


@dataclass
class TransactionCost:
    """Slippage and commission parameters for a trading run.

    Attributes
    ----------
    slippage_bps : float
        Expected slippage in basis points (1 bp = 0.01%).
    commission_bps : float
        Commission rate in basis points of notional (legacy simplified model).
    min_commission : float
        Minimum commission per trade in base currency (legacy simplified model).
    broker_fee : str | None
        Real broker fee schedule to use: "us_stock", "hk_stock", or None (legacy).
    """

    slippage_bps: float = 5.0
    commission_bps: float = 1.0
    min_commission: float = 1.0
    broker_fee: str | None = None

    def calculate_fee(self, qty: int, price: float, side: str = "buy", **kwargs) -> float:
        """Calculate total fees for a trade.

        Uses real broker fee schedule when broker_fee is set, otherwise falls
        back to the legacy simplified model (commission_bps × notional).

        Parameters
        ----------
        qty : int
            Number of shares.
        price : float
            Execution price per share.
        side : str
            "buy" or "sell".
        **kwargs
            Passed to fee schedule (e.g. is_etf for HK).

        Returns
        -------
        float
            Total fees in base currency.
        """
        notional = qty * price
        if self.broker_fee == "us_stock":
            return BrokerFeeSchedule.us_stock(qty, notional, side)
        elif self.broker_fee == "hk_stock":
            is_etf = kwargs.get("is_etf", False)
            return BrokerFeeSchedule.hk_stock(qty, notional, side, is_etf=is_etf)
        else:
            # Legacy simplified model
            return max(self.min_commission, notional * self.commission_bps / 10000)

    @classmethod
    def for_market(cls, market: str, **overrides) -> TransactionCost:
        """Create a TransactionCost with defaults for the given market.

        Auto-enables real broker fees (us_stock / hk_stock) by default.
        Set broker_fee=None in overrides to use the legacy simplified model.

        Parameters
        ----------
        market : str
            One of 'us', 'hk', 'crypto'.
        **overrides
            Optional overrides for slippage_bps / commission_bps / min_commission / broker_fee.
        """
        defaults = _MARKET_DEFAULTS.get(market, _MARKET_DEFAULTS["us"])

        # broker_fee: explicit override > market default (auto-enable) > None
        _SENTINEL = object()
        broker_fee = overrides.pop("broker_fee", _SENTINEL)
        if broker_fee is _SENTINEL:
            broker_fee = _MARKET_BROKER_FEE.get(market)

        return cls(
            slippage_bps=overrides.get("slippage_bps", defaults[0]),
            commission_bps=overrides.get("commission_bps", defaults[1]),
            min_commission=overrides.get("min_commission", defaults[2]),
            broker_fee=broker_fee,
        )

    @classmethod
    def from_config(cls, cfg: dict | None, market: str = "us") -> TransactionCost:
        """Create from a config dict (e.g. broker.paper or broker.live section).

        Falls back to market-aware defaults when keys are missing or None.
        broker_fee is auto-enabled per market unless explicitly set to None in config.
        """
        if cfg is None:
            cfg = {}
        # Build overrides: include keys even when value is None
        # (None means "explicitly disable", distinct from "not present")
        overrides = {
            k: cfg[k]
            for k in ("slippage_bps", "commission_bps", "min_commission", "broker_fee")
            if k in cfg
        }
        return cls.for_market(market, **overrides)
