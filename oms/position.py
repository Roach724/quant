class PositionTracker:
    def __init__(self, broker):
        self.broker = broker
        self._positions: dict[str, float] = {}

    def record_fill(self, symbol, side, qty):
        delta = qty if side == "buy" else -qty
        self._positions[symbol] = self._positions.get(symbol, 0) + delta

    async def reconcile(self):
        broker_pos = await self.broker.get_positions()
        issues = []
        broker_map = {p.symbol: p.qty for p in broker_pos}
        for sym, local_qty in self._positions.items():
            bq = broker_map.get(sym, 0)
            if local_qty != bq:
                issues.append(f"{sym}: local={local_qty}, broker={bq}")
        for sym in broker_map:
            if sym not in self._positions:
                issues.append(f"{sym}: missing local, broker={broker_map[sym]}")
        return issues

    @property
    def positions(self):
        return dict(self._positions)
