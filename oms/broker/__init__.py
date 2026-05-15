from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Protocol
import uuid
import random


@dataclass
class BrokerOrder:
    broker_id: str
    symbol: str
    side: str
    qty: int
    filled_qty: int = 0
    status: str = "pending"
    avg_price: float | None = None
    order_type: str = "market"
    limit_price: float | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class BrokerPosition:
    symbol: str
    qty: int
    avg_entry_price: float
    market_value: float
    unrealized_pnl: float


@dataclass
class BrokerAccount:
    cash: float
    equity: float
    buying_power: float
    positions: list[BrokerPosition] = field(default_factory=list)


class Broker(Protocol):
    async def submit_order(self, symbol: str, side: str, qty: int,
                           order_type: str = "market",
                           limit_price: float | None = None) -> BrokerOrder: ...
    async def cancel_order(self, broker_id: str) -> bool: ...
    async def get_order(self, broker_id: str) -> BrokerOrder: ...
    async def get_positions(self) -> list[BrokerPosition]: ...
    async def get_account(self) -> BrokerAccount: ...
    async def get_open_orders(self) -> list[BrokerOrder]: ...


class PaperBroker:
    def __init__(self, initial_capital: float = 100_000.0):
        self.cash = initial_capital
        self._positions: dict[str, BrokerPosition] = {}
        self._orders: dict[str, BrokerOrder] = {}
        self._price = 100.0

    async def submit_order(self, symbol, side, qty, order_type="market", limit_price=None):
        oid = str(uuid.uuid4())[:8]
        now = datetime.now(timezone.utc)
        order = BrokerOrder(
            broker_id=oid, symbol=symbol, side=side, qty=qty,
            order_type=order_type, limit_price=limit_price,
            created_at=now, updated_at=now,
        )
        if order_type == "limit":
            order.status = "pending"
            self._orders[oid] = order
            return order
        # Market order: fill immediately
        self._price += random.uniform(-0.5, 0.5)
        fill_price = self._price
        cost = fill_price * qty
        self.cash -= cost
        order.status = "filled"
        order.filled_qty = qty
        order.avg_price = fill_price
        order.updated_at = datetime.now(timezone.utc)
        # Update position
        pos = self._positions.get(symbol)
        if pos:
            total_qty = pos.qty + (qty if side == "buy" else -qty)
            if total_qty > 0:
                pos.avg_entry_price = ((pos.avg_entry_price * pos.qty) + (fill_price * qty)) / (pos.qty + qty)
            pos.qty = total_qty
        else:
            qty_signed = qty if side == "buy" else -qty
            self._positions[symbol] = BrokerPosition(
                symbol=symbol, qty=qty_signed,
                avg_entry_price=fill_price,
                market_value=fill_price * abs(qty_signed),
                unrealized_pnl=0.0,
            )
        self._orders[oid] = order
        return order

    async def cancel_order(self, broker_id):
        if broker_id in self._orders:
            self._orders[broker_id].status = "cancelled"
            return True
        return False

    async def get_order(self, broker_id):
        return self._orders.get(broker_id)

    async def get_positions(self):
        return list(self._positions.values())

    async def get_account(self):
        equity = self.cash + sum(p.market_value for p in self._positions.values())
        return BrokerAccount(
            cash=self.cash, equity=equity,
            buying_power=equity * 2,
            positions=list(self._positions.values()),
        )

    async def get_open_orders(self):
        return [o for o in self._orders.values() if o.status in ("pending",)]
