"""Post-trade risk monitor — periodic polling of broker state with threshold checks.

Runs on a configurable interval via asyncio. Computes drawdown, exposure, leverage
from live broker account data and fires alerts on threshold breaches.
"""

import asyncio
from datetime import datetime, timezone


class RiskMonitor:
    """Periodic post-trade risk monitor.

    Polls the broker for account + position data, compares current state
    against configured thresholds, and fires alerts via AlertManager.
    """

    def __init__(self, broker, alert_manager, config: dict | None = None):
        self.broker = broker
        self.alerts = alert_manager
        self.config = config or {}
        self._running = False
        self._task = None
        self._peak_equity = 0

    async def start(self, interval_seconds: int = 30):
        """Start periodic monitoring. Non-blocking — runs as background task."""
        self._running = True
        self._task = asyncio.create_task(self._loop(interval_seconds))

    async def stop(self):
        self._running = False
        if self._task:
            self._task.cancel()

    async def _loop(self, interval: int):
        while self._running:
            try:
                await self.check()
            except Exception:
                pass
            await asyncio.sleep(interval)

    async def check(self):
        """Run one round of risk checks."""
        account = await self.broker.get_account()
        positions = await self.broker.get_positions()

        # Track peak equity for drawdown calculation
        if account.equity > self._peak_equity:
            self._peak_equity = account.equity

        # Drawdown check
        max_dd = self.config.get("max_drawdown", 0.20)
        if self._peak_equity > 0:
            dd = (account.equity - self._peak_equity) / self._peak_equity
            if dd < -max_dd:
                self.alerts.fire("critical",
                    f"Max drawdown breached: {abs(dd):.2%} (limit: {max_dd:.0%})",
                    {"drawdown": round(dd, 4), "peak_equity": self._peak_equity,
                     "current_equity": account.equity})

        # Leverage check
        max_lev = self.config.get("max_leverage", 2.0)
        gross = sum(abs(p.market_value) for p in positions)
        leverage = gross / account.equity if account.equity > 0 else 0
        if leverage > max_lev:
            self.alerts.fire("warning",
                f"Leverage limit exceeded: {leverage:.1f}x (limit: {max_lev:.1f}x)",
                {"leverage": round(leverage, 2), "gross_exposure": gross})

        # Concentration check
        max_conc = self.config.get("max_concentration", 0.30)
        for pos in positions:
            if account.equity > 0:
                conc = pos.market_value / account.equity
                if conc > max_conc:
                    self.alerts.fire("warning",
                        f"Position concentration high: {pos.symbol} at {conc:.1%}",
                        {"symbol": pos.symbol, "concentration": round(conc, 4)})

        # Cash / margin check
        min_cash = self.config.get("min_cash_ratio", 0.05)
        if account.equity > 0 and account.cash / account.equity < min_cash:
            self.alerts.fire("warning",
                f"Low cash: {account.cash:,.0f} ({account.cash/account.equity:.1%} of equity)",
                {"cash": account.cash, "equity": account.equity})
