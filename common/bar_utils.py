"""Bar data utilities — freshness checks for replay/stale detection."""

from __future__ import annotations

from datetime import UTC, datetime


def is_stale_bar(
    bar_ts: datetime | str,
    bar_period_sec: float = 300,
    factor: float = 1.5,
) -> bool:
    """Return True if bar is old enough to be considered replay/stale.

    Used to prevent historical bars (replayed from BQDataSource poll catch-up
    after a restart) from triggering real trades.

    Args:
        bar_ts: bar timestamp (datetime or ISO-format string).
        bar_period_sec: expected bar cycle in seconds (e.g. 300 for 5m).
        factor: a fresh bar must be ≤ factor × bar_period_sec old.
    """
    if isinstance(bar_ts, str):
        bar_ts = datetime.fromisoformat(bar_ts)
    age = (datetime.now(UTC) - bar_ts).total_seconds()
    return age > bar_period_sec * factor
