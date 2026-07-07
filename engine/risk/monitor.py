"""Post-trade risk monitor — background thread for periodic risk health checks.

Runs independently from the main trading loop as a daemon thread.
Logs warnings on threshold breaches; advisory only (does not stop trading).
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)


class RiskMonitor:
    """Periodic sync risk monitor.

    Takes a *state_provider* callable that returns a dict:
        {"equity": float, "cash": float, "positions": list[dict]}
    where each position has {"symbol", "qty", "market_value"}.
    """

    def __init__(
        self,
        state_provider: Callable[[], dict[str, Any] | None],
        config: dict[str, Any] | None = None,
    ):
        self._provider = state_provider
        self._config = config or {}
        self._running = False
        self._thread: threading.Thread | None = None
        self._peak_equity = 0.0

    # ── Lifecycle ──

    def start(self, interval_sec: int = 60):
        """Start periodic monitoring in a daemon thread."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._loop, args=(interval_sec,), daemon=True)
        self._thread.start()
        logger.info("RiskMonitor: started (interval=%ds)", interval_sec)

    def stop(self):
        """Signal the monitor to stop. Thread exits on next sleep cycle."""
        self._running = False

    # ── Internal ──

    def _loop(self, interval: int):
        while self._running:
            try:
                self._check()
            except Exception:
                logger.exception("RiskMonitor: check failed")
            time.sleep(interval)

    def _check(self):
        state = self._provider()
        if state is None:
            return

        equity = float(state.get("equity", 0))
        cash = float(state.get("cash", 0))
        positions: list[dict] = state.get("positions", []) or []

        # Track peak equity
        if equity > self._peak_equity:
            self._peak_equity = equity

        # ── Drawdown ──
        max_dd = float(self._config.get("max_drawdown", 0.15))
        if self._peak_equity > 0:
            dd = (equity - self._peak_equity) / self._peak_equity
            if dd < -max_dd:
                logger.warning(
                    "RiskMonitor: drawdown %.1f%% exceeds limit %.1f%% (peak=%.0f eq=%.0f)",
                    abs(dd) * 100,
                    max_dd * 100,
                    self._peak_equity,
                    equity,
                )

        # ── Leverage ──
        max_lev = float(self._config.get("max_leverage", 1.0))
        gross = sum(abs(p.get("market_value", 0)) for p in positions)
        if equity > 0 and gross / equity > max_lev:
            logger.warning(
                "RiskMonitor: leverage %.1fx > limit %.1fx (gross=%.0f eq=%.0f)",
                gross / equity,
                max_lev,
                gross,
                equity,
            )

        # ── Concentration ──
        max_conc = float(self._config.get("max_concentration", 0.30))
        for p in positions:
            mv = abs(p.get("market_value", 0))
            if equity > 0 and mv / equity > max_conc:
                logger.warning(
                    "RiskMonitor: %s concentration %.1f%% > limit %.1f%% (mv=%.0f eq=%.0f)",
                    p.get("symbol", "?"),
                    mv / equity * 100,
                    max_conc * 100,
                    mv,
                    equity,
                )

        # ── Cash ratio ──
        min_cash_ratio = float(self._config.get("min_cash_ratio", 0.05))
        if equity > 0 and cash / equity < min_cash_ratio:
            logger.warning(
                "RiskMonitor: low cash %.0f (%.1f%% of equity %.0f)",
                cash,
                cash / equity * 100,
                equity,
            )

        # Periodic heartbeat
        logger.debug(
            "RiskMonitor: eq=%.0f dd=%.1f%% pos=%d",
            equity,
            (equity - self._peak_equity) / max(self._peak_equity, 1) * 100,
            len(positions),
        )
