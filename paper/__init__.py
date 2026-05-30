"""Paper trading replay system — multi-market historical bar replay with simulated execution."""

from paper.market import MARKET_US, MARKET_HK, MARKET_CRYPTO, MARKET_SCHEDULES, is_market_open
from paper.strategies import BuyHold, SimpleMomentum

__all__ = [
    "MARKET_US", "MARKET_HK", "MARKET_CRYPTO",
    "MARKET_SCHEDULES", "is_market_open",
    "BuyHold", "SimpleMomentum",
]
