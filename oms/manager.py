from datetime import datetime
from oms.state import TrackedOrder


class OrderManager:
    def __init__(self, broker):
        self.broker = broker
        self.orders: dict[str, TrackedOrder] = {}
        self._history: list[TrackedOrder] = []

    async def submit(self, symbol, side, qty, order_type="market",
                     strategy_name="", signal_id=None, limit_price=None):
        t = TrackedOrder(
            symbol=symbol, side=side, qty=qty,
            strategy_name=strategy_name, signal_id=signal_id,
        )
        t.transition("SUBMITTED")
        try:
            broker_order = await self.broker.submit_order(
                symbol, side, qty, order_type, limit_price,
            )
            t.broker_id = broker_order.broker_id
            t.state = broker_order.status.upper().replace(" ", "_")
            t.filled_qty = broker_order.filled_qty
            t.avg_fill_price = broker_order.avg_price
            t.updated_at = datetime.now()
        except Exception:
            t.transition("REJECTED")
        self.orders[t.internal_id] = t
        self._history.append(t)
        return t

    async def cancel(self, internal_id):
        t = self.orders.get(internal_id)
        if t and t.broker_id:
            ok = await self.broker.cancel_order(t.broker_id)
            if ok:
                t.transition("CANCELLED")
            return ok
        return False

    def get_open_orders(self):
        return [
            o for o in self.orders.values()
            if o.state in ("PENDING", "SUBMITTED", "ACKNOWLEDGED", "PARTIAL_FILL")
        ]

    def get_order_history(self, since=None):
        if since:
            return [o for o in self._history if o.created_at >= since]
        return list(self._history)
