"""Paper trading replay system — multi-market historical bar replay with simulated execution."""

from paper.market import MARKET_US, MARKET_HK, MARKET_CRYPTO, MARKET_SCHEDULES, is_market_open
from strategies import BuyHold, SimpleMomentum  # migrated to strategies/ (single source of truth)

__all__ = [
    "MARKET_US", "MARKET_HK", "MARKET_CRYPTO",
    "MARKET_SCHEDULES", "is_market_open",
    "BuyHold", "SimpleMomentum",
]
