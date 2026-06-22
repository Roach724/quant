"""Bar data utilities — freshness checks for replay/stale detection.

Handles the BQ timezone quirk: Futu OpenD returns market-local timestamps
(e.g. "09:35" for US ET / "15:55" for HK HKT), but BQ stores them with a
UTC offset.  This module corrects by interpreting the raw timestamp in the
market's actual timezone, then comparing to real UTC now.

Do NOT store 'corrected' data in BQ — correcting at the source would break
existing downstream queries that assume market-local semantics.  All consumers
that need absolute UTC should use is_stale_bar() with the correct market.

See HANDBOOK.md §X for the full explanation.
"""

from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

_MARKET_TZ: dict[str, str] = {
    "us": "America/New_York",
    "hk": "Asia/Hong_Kong",
    "crypto": "UTC",
}


def is_stale_bar(
    bar_ts: datetime | str,
    bar_period_sec: float = 300,
    factor: float = 1.5,
    market: str = "us",
) -> bool:
    """Return True if bar is old enough to be considered replay/stale.

    Args:
        bar_ts: bar timestamp (datetime or ISO-format string).
        bar_period_sec: expected bar cycle in seconds (e.g. 300 for 5m).
        factor: a fresh bar must be ≤ factor × bar_period_sec old.
        market: trading market ("us"|"hk"|"crypto"), used to correct the BQ
                timezone offset for market-local timestamps.
    """
    if isinstance(bar_ts, str):
        bar_ts = datetime.fromisoformat(bar_ts)
    # BQ stores market-local time as UTC → correct to real UTC
    bar_naive = bar_ts.replace(tzinfo=None)
    tz_name = _MARKET_TZ.get(market, "UTC")
    bar_real_utc = bar_naive.replace(tzinfo=ZoneInfo(tz_name)).astimezone(UTC)
    age = (datetime.now(UTC) - bar_real_utc).total_seconds()
    return age > bar_period_sec * factor
