"""Market constants and trading-hour utilities for the paper runner.

Supports US, HK, and Crypto markets with per-market trading schedules,
fee structures, and session-timezone logic.
"""

from datetime import datetime, time, timezone
from typing import Tuple
import logging

log = logging.getLogger("paper.market")

# ── Market type constants ──
MARKET_US = "us"
MARKET_HK = "hk"
MARKET_CRYPTO = "crypto"

# ── Per-market schedule & fee configs (times in UTC) ──
MARKET_SCHEDULES = {
    "us": {
        "open": (13, 30),          # 9:30 ET → 13:30 UTC (winter) / 14:30 UTC (summer)
        "close": (20, 0),          # 16:00 ET → 20:00 UTC (winter) / 21:00 UTC (summer)
        "timezone": "America/New_York",
        "weekdays_only": True,
        "min_commission": 1.0,
        "slippage_bps": 5.0,
        "commission_bps": 1.0,
        "currency": "USD",
    },
    "hk": {
        "open": (1, 30),           # 9:30 HKT → 1:30 UTC
        "close": (8, 0),           # 16:00 HKT → 8:00 UTC
        "lunch_start": (4, 0),     # 12:00 HKT → 4:00 UTC
        "lunch_end": (5, 0),       # 13:00 HKT → 5:00 UTC
        "timezone": "Asia/Hong_Kong",
        "weekdays_only": True,
        "min_commission": 15.0,
        "slippage_bps": 5.0,
        "commission_bps": 3.0,
        "currency": "HKD",
    },
    "crypto": {
        "open": (0, 0),
        "close": (23, 59),
        "timezone": "UTC",
        "weekdays_only": False,
        "min_commission": 0.0,
        "slippage_bps": 2.0,
        "commission_bps": 0.5,
        "currency": "USDT",
    },
}

# ── Default symbol pools per market ──
DEFAULT_SYMBOLS = {
    "us": ["AAPL", "MSFT", "GOOGL", "AMZN", "META", "TSLA", "NVDA", "JPM"],
    "hk": ["0700", "9988", "0941", "0005", "0388", "1299", "2318", "1810"],
    "crypto": ["BTC", "ETH", "SOL", "BNB", "XRP", "ADA", "DOGE"],
}


def is_market_open(market: str, dt: datetime) -> bool:
    """Return True if *market* is open at the given UTC *dt*.

    For simplicity this uses the fixed UTC windows in MARKET_SCHEDULES
    (assuming approximate winter-time offsets).  Day-of-week rules and
    HK lunch break are enforced.

    If the timestamp has time 00:00:00 (midnight), it is treated as
    a daily bar — only weekday checks apply (HK lunch is ignored).
    """
    schedule = MARKET_SCHEDULES.get(market)
    if not schedule:
        log.warning("Unknown market %r — assuming always open", market)
        return True

    # Day-of-week check
    if schedule["weekdays_only"] and dt.weekday() >= 5:
        return False

    # If time is midnight, treat as daily bar → open on weekdays only
    t = dt.time()
    if t == time(0, 0):
        return True

    open_t = time(*schedule["open"])
    close_t = time(*schedule["close"])

    # HK lunch break
    if market == "hk":
        lunch_start = time(*schedule["lunch_start"])
        lunch_end = time(*schedule["lunch_end"])
        if lunch_start <= t < lunch_end:
            return False

    if open_t <= close_t:
        return open_t <= t <= close_t
    else:
        # Overnight session (e.g. open 20:00, close 04:00)
        return t >= open_t or t <= close_t


def trading_hours_for(market: str) -> dict:
    """Return a human-readable description of trading hours."""
    schedule = MARKET_SCHEDULES.get(market)
    if not schedule:
        return {"market": market, "status": "unknown"}

    return {
        "market": market,
        "currency": schedule["currency"],
        "weekdays_only": schedule["weekdays_only"],
        "utc_open": f"{schedule['open'][0]:02d}:{schedule['open'][1]:02d}",
        "utc_close": f"{schedule['close'][0]:02d}:{schedule['close'][1]:02d}",
        "slippage_bps": schedule["slippage_bps"],
        "commission_bps": schedule["commission_bps"],
        "min_commission": schedule["min_commission"],
    }


def default_symbols_for(market: str) -> list[str]:
    """Return the default symbol list for a market."""
    return DEFAULT_SYMBOLS.get(market, [])
