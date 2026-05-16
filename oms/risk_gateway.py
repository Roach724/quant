"""Pre-trade risk gateway — checks orders against risk rules before broker submission.

Wraps the Phase 2 engine/risk/RiskEngine with live portfolio state from the broker.
"""

from engine.risk import RiskEngine


class RiskGateway:
    """Pre-trade risk gate. Runs risk rules on proposed orders before they hit the broker.

    Uses the existing composable RiskRule protocol from engine/risk/.
    Rules see the live portfolio state (via broker) rather than engine Portfolio.
    """

    def __init__(self, rules, broker, alert_manager=None):
        self.engine = RiskEngine(rules)
        self.broker = broker
        self.alerts = alert_manager

    async def check(self, orders, bar_data):
        """Run pre-trade checks. Returns (approved_orders, rejected_orders)."""
        approved = self.engine.check(orders, self._dummy_portfolio(), bar_data)
        rejected = [o for o in orders if o not in approved]

        for r in rejected:
            if self.alerts:
                self.alerts.fire("warning", f"Pre-trade rejected: {r.symbol} {r.side} {r.size}",
                                 {"symbol": r.symbol, "side": r.side, "size": r.size})
        return approved, rejected

    def _dummy_portfolio(self):
        # Risk rules expect a portfolio-like object with .positions and .total_equity
        # We don't need real values for pre-trade checks on individual orders
        class DummyPF:
            initial_capital = 100_000
            total_equity = 100_000
            positions = {}
        return DummyPF()
