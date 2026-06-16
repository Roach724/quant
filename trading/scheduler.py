"""RebalanceScheduler — strategy rebalancing scheduler.

Determines whether a strategy should trade on a given bar,
based on K-line frequency, lookback window, and rebalance interval.
Pure state machine — no external dependencies.
"""

from __future__ import annotations

import json
import logging
from enum import Enum
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class Decision(str, Enum):
    TRADE = "trade"        # A rebalance window has been reached — execute signals
    SKIP = "skip"          # Within a rebalance window — do nothing
    WAITING = "waiting"    # Lookback phase — not enough data yet


class RebalanceScheduler:
    """Per-strategy rebalance scheduler.

    Tracks the current bar count (excluding lookback) and the last bar
    on which a rebalance was executed.  Answers one question per bar:
    should the strategy generate and execute signals right now?
    """

    def __init__(
        self,
        freq_minutes: int,
        lookback_bars: int,
        rebalance_every: int,
        state_path: str,
    ):
        if freq_minutes < 1:
            raise ValueError(f"freq_minutes must be >= 1, got {freq_minutes}")
        if lookback_bars < 0:
            raise ValueError(f"lookback_bars must be >= 0, got {lookback_bars}")
        if rebalance_every < 1:
            raise ValueError(f"rebalance_every must be >= 1, got {rebalance_every}")

        self.freq_minutes = freq_minutes
        self.lookback_bars = lookback_bars
        self.rebalance_every = rebalance_every
        self.state_path = Path(state_path)

        # Derived
        self.rebalance_interval_minutes = freq_minutes * rebalance_every

        # State
        self.bar_count = 0       # live bar count (excludes lookback)
        self.last_rebalance_bar: Optional[int] = None  # bar index of last trade

        # Warn on short rebalance intervals
        if self.rebalance_interval_minutes < 60:
            logger.warning(
                "Rebalance interval is only %d minutes (freq=%dm x every=%d). "
                "This may trigger broker frequent-trading restrictions.",
                self.rebalance_interval_minutes,
                freq_minutes,
                rebalance_every,
            )

    def on_bar(self) -> Decision:
        """Call once per bar. Returns whether to trade now."""
        self.bar_count += 1

        if self.bar_count < self.lookback_bars:
            return Decision.WAITING

        if self.last_rebalance_bar is None:
            self.last_rebalance_bar = self.bar_count
            return Decision.TRADE

        gap = self.bar_count - self.last_rebalance_bar
        if gap >= self.rebalance_every:
            self.last_rebalance_bar = self.bar_count
            return Decision.TRADE

        return Decision.SKIP

    def save(self) -> dict:
        return {
            "bar_count": self.bar_count,
            "last_rebalance_bar": self.last_rebalance_bar,
        }

    def load_state(self, bar_count: int, last_rebalance_bar: int | None) -> None:
        self.bar_count = bar_count
        self.last_rebalance_bar = last_rebalance_bar

    def write(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(json.dumps(self.save(), indent=2))

    @classmethod
    def from_file(cls, file_path: str, **kwargs) -> "RebalanceScheduler":
        path = Path(file_path)
        if path.exists():
            state = json.loads(path.read_text())
            sched = cls(state_path=file_path, **kwargs)
            sched.load_state(state["bar_count"], state["last_rebalance_bar"])
            return sched
        return cls(state_path=file_path, **kwargs)
