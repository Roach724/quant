"""Post-trade risk monitor — background thread polling runner state per bar.

Activated by both live and trading runners on startup.
Checks: leverage, concentration, drawdown, cash buffer.
Logs WARNING on threshold breaches.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Callable

logger = logging.getLogger(__name__)

CheckFn = Callable[[], dict]  # returns {metric_name: value, ...}


class RiskMonitor:
    """Sync background monitor polling runner state at fixed intervals.

    Takes callables that report current state (equity, cash, positions) and
    compares against configured thresholds.
    """

    def __init__(self, config: dict | None = None):
        self._config = config or {}
        self._checks: list[tuple[str, CheckFn]] = []
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._interval = int(self._config.get("monitor_interval_sec", 60))

    def add_check(self, name: str, fn: CheckFn) -> None:
        """Register a check function.

        fn() should return a dict like {"equity": 100000, "positions_value": 50000, "cash": 50000, ...}
        """
        self._checks.append((name, fn))

    def start(self) -> None:
        """Start background monitoring thread."""
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="risk-monitor")
        self._thread.start()
        logger.info("RiskMonitor started (interval=%ds, %d checks)", self._interval, len(self._checks))

    def stop(self) -> None:
        """Stop background monitoring."""
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)
            self._thread = None
        logger.info("RiskMonitor stopped")

    def _loop(self) -> None:
        peak_equity = 0.0
        max_drawdown = float(self._config.get("max_drawdown", 0.15))
        max_leverage = float(self._config.get("max_leverage", 1.0))
        max_concentration = float(self._config.get("max_concentration", 0.50))
        min_cash_pct = float(self._config.get("min_cash_buffer", 0.01))

        while not self._stop.wait(self._interval):
            try:
                for name, fn in self._checks:
                    state = fn()
                    if not state:
                        continue

                    equity = state.get("equity", 0)
                    cash = state.get("cash", 0)
                    pos_value = state.get("positions_value", 0)
                    positions = state.get("positions", {})  # {symbol: value}

                    # ── Drawdown ──
                    if equity > peak_equity:
                        peak_equity = equity
                    if peak_equity > 0:
                        dd = (peak_equity - equity) / peak_equity
                        if dd >= max_drawdown:
                            logger.warning(
                                "RiskMonitor [%s]: DRAWDOWN %.1f%% >= %.1f%% (peak=%.0f eq=%.0f)",
                                name, dd * 100, max_drawdown * 100, peak_equity, equity,
                            )

                    # ── Leverage ──
                    if equity > 0:
                        lev = pos_value / equity
                        if lev > max_leverage:
                            logger.warning(
                                "RiskMonitor [%s]: LEVERAGE %.1f%% > %.1f%% (pos=%.0f eq=%.0f)",
                                name, lev * 100, max_leverage * 100, pos_value, equity,
                            )

                    # ── Concentration ──
                    if equity > 0 and positions:
                        for sym, val in positions.items():
                            conc = val / equity
                            if conc > max_concentration:
                                logger.warning(
                                    "RiskMonitor [%s]: CONCENTRATION %s=%.1f%% > %.1f%%",
                                    name, sym, conc * 100, max_concentration * 100,
                                )

                    # ── Cash buffer ──
                    if equity > 0:
                        cash_pct = cash / equity
                        if cash_pct < min_cash_pct:
                            logger.warning(
                                "RiskMonitor [%s]: CASH LOW %.1f%% < %.1f%% (cash=%.0f eq=%.0f)",
                                name, cash_pct * 100, min_cash_pct * 100, cash, equity,
                            )

            except Exception:
                logger.debug("RiskMonitor check error", exc_info=True)
