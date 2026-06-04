"""MarketCalendar — trading schedule with holiday awareness.

Uses exchange_calendars for automatic holiday detection (including
lunar-based HK holidays). Falls back to weekday + fixed-hour check
if the library is unavailable.

Supported markets:
- us  → XNYS (NYSE)
- hk  → XHKG (HKEX)
- crypto → always open
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta, time as _time
from typing import Optional

logger = logging.getLogger(__name__)

# ── Market hours in UTC (covers both DST and standard time) ──────────
# US Eastern: 9:30-16:00  → 13:30-20:00 UTC (DST) / 14:30-21:00 UTC (standard)
# HK:          9:30-16:00  → 01:30-08:00 UTC (lunch 04:00-05:00)
_MARKET_HOURS = {
    "us": {"open": (13, 30), "close": (20, 0), "open_alt": (14, 30), "close_alt": (21, 0)},
    "hk": {"open": (1, 30), "close": (8, 0),
           "lunch_start": (4, 0), "lunch_end": (5, 0)},
}

_CALENDAR_CODES = {"us": "XNYS", "hk": "XHKG"}


class MarketCalendar:
    """Trading calendar for a single market.

    Parameters
    ----------
    market : str
        Market code: "us", "hk", or "crypto".
    """

    def __init__(self, market: str):
        self.market = market.lower()
        self._cal = None  # exchange_calendars calendar object

        if self.market == "crypto":
            return  # always open, no calendar needed

        code = _CALENDAR_CODES.get(self.market)
        if code is None:
            logger.warning("Unknown market '%s' — treating as always open", market)
            return

        try:
            import exchange_calendars as xcals
            self._cal = xcals.get_calendar(code)
            logger.info("MarketCalendar: using exchange_calendars (%s)", code)
        except ImportError:
            logger.warning(
                "exchange_calendars not installed — "
                "using basic weekday+hours check (no holiday awareness)"
            )
        except Exception:
            logger.exception("Failed to load exchange_calendars for %s", code)

    # ── Public API ───────────────────────────────────────────────────

    def is_trading_day(self, dt: Optional[datetime] = None) -> bool:
        """Check if *dt* falls on a trading day (session date).

        Does NOT check trading hours — only whether the exchange is open
        on that calendar date.
        """
        if self.market == "crypto":
            return True
        if dt is None:
            dt = datetime.now(timezone.utc)

        if self._cal is not None:
            try:
                return self._cal.is_session(self._to_date(dt))
            except Exception:
                logger.exception("exchange_calendars is_session failed")

        # Fallback: weekday check only (no holidays)
        return dt.weekday() < 5

    def is_open_now(self, preheat_minutes: int = 0) -> bool:
        """Check if the market is open right now (trading day + within hours).

        Args:
            preheat_minutes: If > 0, return True up to this many minutes
                before the official open time (for early subscription).
        """
        if self.market == "crypto":
            return True

        now = datetime.now(timezone.utc)

        # Must be a trading day
        if not self.is_trading_day(now):
            return False

        # Check trading hours (with preheat window)
        hours = _MARKET_HOURS.get(self.market)
        if not hours:
            return True

        t = now.time()

        # HK lunch break
        if "lunch_start" in hours:
            ls = _time(*hours["lunch_start"])
            le = _time(*hours["lunch_end"])
            if ls <= t < le:
                return False

        # Calculate preheat-open: official open minus preheat minutes
        preheat_dt = datetime.now(timezone.utc) + timedelta(minutes=preheat_minutes)
        preheat_t = preheat_dt.time()

        # Check if within main session (either DST or standard)
        open_t = _time(*hours["open"])
        close_t = _time(*hours["close"])
        if preheat_t >= open_t and t <= close_t:
            return True

        # Alternative hours (US standard time: 14:30-21:00)
        if "open_alt" in hours:
            alt_open = _time(*hours["open_alt"])
            alt_close = _time(*hours["close_alt"])
            if preheat_t >= alt_open and t <= alt_close:
                return True

        return False

    def time_until_open(self) -> float:
        """Seconds until the next market open.

        Returns 0 if the market is currently open.
        """
        if self.market == "crypto":
            return 0.0

        now = datetime.now(timezone.utc)

        if self.is_open_now():
            return 0.0

        if self._cal is not None:
            try:
                next_open = self._cal.next_open(self._to_ts(now))
                if next_open is not None:
                    delta = (next_open.to_pydatetime() - now).total_seconds()
                    return max(0.0, float(delta))
            except Exception:
                logger.exception("exchange_calendars next_open failed")

        # Fallback: calculate next open manually
        return self._fallback_time_until_open(now)

    def time_until_close(self) -> float:
        """Seconds until market close today. Returns 0 if not in session."""
        if self.market == "crypto":
            return float("inf")

        now = datetime.now(timezone.utc)

        if self._cal is not None:
            try:
                next_close = self._cal.next_close(self._to_ts(now))
                if next_close is not None:
                    delta = (next_close.to_pydatetime() - now).total_seconds()
                    return max(0.0, float(delta))
            except Exception:
                logger.exception("exchange_calendars next_close failed")

        # Fallback
        if not self.is_open_now():
            return 0.0

        hours = _MARKET_HOURS.get(self.market, {})
        import datetime as _dt
        close_t = _dt.time(*hours.get("close", (0, 0)))
        now_t = now.time()
        # Convert to seconds
        close_sec = close_t.hour * 3600 + close_t.minute * 60
        now_sec = now_t.hour * 3600 + now_t.minute * 60
        if close_sec > now_sec:
            return float(close_sec - now_sec)
        return 0.0

    def next_open_datetime(self) -> datetime:
        """Return the datetime of the next market open (UTC)."""
        if self.market == "crypto":
            return datetime.now(timezone.utc)

        now = datetime.now(timezone.utc)

        if self._cal is not None:
            try:
                ts = self._cal.next_open(self._to_ts(now))
                if ts is not None:
                    return ts.to_pydatetime()
            except Exception:
                logger.exception("exchange_calendars next_open failed")

        # Fallback
        seconds = self._fallback_time_until_open(now)
        return now + timedelta(seconds=seconds)

    @property
    def has_calendar(self) -> bool:
        """True if using exchange_calendars (holiday-aware)."""
        return self._cal is not None

    # ── Internals ────────────────────────────────────────────────────

    @staticmethod
    def _to_ts(dt):
        """Convert datetime to timezone-aware pandas Timestamp.

        Uses zoneinfo.ZoneInfo('UTC') for compatibility with exchange_calendars
        on Python 3.10 (datetime.timezone.utc lacks .key attribute).
        """
        import pandas as pd
        import zoneinfo
        if isinstance(dt, pd.Timestamp):
            if dt.tz is None:
                return dt.tz_localize(zoneinfo.ZoneInfo("UTC"))
            return dt
        naive = dt.replace(tzinfo=None)
        return pd.Timestamp(naive, tz=zoneinfo.ZoneInfo("UTC"))

    @staticmethod
    def _to_date(dt):
        """Convert datetime to date-only (midnight) naive Timestamp for is_session."""
        import pandas as pd
        if isinstance(dt, pd.Timestamp):
            naive = dt.tz_localize(None) if dt.tz is not None else dt
        else:
            naive = pd.Timestamp(dt.replace(tzinfo=None))
        return naive.normalize()  # sets time to 00:00:00

    def _fallback_time_until_open(self, now: datetime) -> float:
        """Calculate seconds until next market open (weekday-based fallback).

        Walks forward in 1-hour increments, checking weekday + hour window.
        """
        hours = _MARKET_HOURS.get(self.market, {})
        if not hours:
            return 0.0

        open_h = hours["open"]
        cursor = now.replace(minute=0, second=0, microsecond=0)
        max_iter = 168  # search up to 1 week

        for _ in range(max_iter):
            wd = cursor.weekday()
            if wd < 5:  # weekday
                open_today = cursor.replace(
                    hour=open_h[0], minute=open_h[1], second=0
                )
                if open_today > now:
                    return (open_today - now).total_seconds()
            cursor += timedelta(hours=1)

        return 3600.0  # safety: retry in 1h
