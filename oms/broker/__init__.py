from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Protocol
import uuid


@dataclass
class BrokerOrder:
    broker_id: str
    symbol: str
    side: str
    qty: float = 0.0
    filled_qty: float = 0.0
    status: str = "pending"
    avg_price: float | None = None
    order_type: str = "market"
    limit_price: float | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class BrokerPosition:
    symbol: str
    qty: float
    avg_entry_price: float
    market_value: float
    unrealized_pnl: float


@dataclass
class BrokerAccount:
    cash: float
    equity: float
    buying_power: float
    positions: list[BrokerPosition] = field(default_factory=list)


@dataclass
class BrokerDeal:
    deal_id: str
    order_id: str
    symbol: str
    side: str
    qty: float
    price: float
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class Broker(Protocol):
    async def submit_order(self, symbol: str, side: str, qty: float,
                           order_type: str = "market",
                           limit_price: float | None = None) -> BrokerOrder: ...
    async def cancel_order(self, broker_id: str) -> bool: ...
    async def get_order(self, broker_id: str) -> BrokerOrder: ...
    async def get_positions(self) -> list[BrokerPosition]: ...
    async def get_account(self) -> BrokerAccount: ...
    async def get_open_orders(self) -> list[BrokerOrder]: ...


class PaperBroker:
    def __init__(self, initial_capital: float = 100_000.0, liquidity_factor: float = 0.05):
        self.cash = initial_capital
        self._positions: dict[str, BrokerPosition] = {}
        self._orders: dict[str, BrokerOrder] = {}
        self._prices: dict[str, float] = {}
        self._volumes: dict[str, float] = {}
        self._liquidity_factor = liquidity_factor
        self._default_price = 100.0

    def update_price(self, symbol: str, price: float, bar_ohlc: dict | None = None):
        """Set the current market price for a symbol. Call before order submission.

        If bar_ohlc is provided (with 'high'/'low'), pending limit orders are
        checked against intra-bar extremes, not just the closing price.
        """
        self._prices[symbol] = price
        # Extract per-symbol bar data (high/low for limit orders, volume for partial fills)
        sym_ohlc = bar_ohlc.get(symbol) if bar_ohlc else None
        if isinstance(sym_ohlc, dict):
            if "volume" in sym_ohlc:
                self._volumes[symbol] = float(sym_ohlc["volume"])
        # Determine the worst-case price for pending limit orders within this bar
        high = sym_ohlc if sym_ohlc else price
        low = sym_ohlc if sym_ohlc else price
        if isinstance(high, dict):
            high = high.get("high", high.get("close", price))
            low = low.get("low", low.get("close", price))
        # Fill pending limit orders that would have been triggered intra-bar
        for oid, order in list(self._orders.items()):
            if order.status == "pending" and order.symbol == symbol:
                if order.side == "buy" and order.limit_price is not None:
                    # Buy limit: filled if low <= limit_price (price dropped to our bid)
                    intra_fill_price = min(order.limit_price, max(low, price)) if low <= order.limit_price else None
                    if intra_fill_price is not None:
                        self._execute_fill(order, intra_fill_price)
                elif order.side == "sell" and order.limit_price is not None:
                    # Sell limit: filled if high >= limit_price (price rose to our ask)
                    intra_fill_price = max(order.limit_price, min(high, price)) if high >= order.limit_price else None
                    if intra_fill_price is not None:
                        self._execute_fill(order, intra_fill_price)

    def _price_for(self, symbol: str) -> float:
        return self._prices.get(symbol, self._default_price)

    def _limit_crossed(self, order: BrokerOrder, current_price: float) -> bool:
        if order.limit_price is None:
            return False
        if order.side == "buy":
            return current_price <= order.limit_price
        return current_price >= order.limit_price

    def _execute_fill(self, order: BrokerOrder, fill_price: float, fill_qty: float | None = None):
        """Execute a fill, optionally partial.  fill_qty=None means full fill."""
        qty = fill_qty if fill_qty is not None else order.qty
        if qty <= 0:
            return
        if order.side == "buy":
            self.cash -= fill_price * qty
        else:
            self.cash += fill_price * qty
        order.filled_qty = qty
        order.avg_price = fill_price
        order.status = "filled" if qty >= order.qty else "partial"
        order.updated_at = datetime.now(timezone.utc)
        # Update broker-side position tracking (cosmetic; runner uses Portfolio.cash)
        pos = self._positions.get(order.symbol)
        qty_signed = qty if order.side == "buy" else -qty
        if pos:
            total_qty = pos.qty + qty_signed
            if total_qty > 0 and order.side == "buy":
                # Only recalc avg_entry on buy; sell leaves avg_entry unchanged
                pos.avg_entry_price = ((pos.avg_entry_price * pos.qty) + (fill_price * qty)) / (pos.qty + qty)
            pos.qty = total_qty
        else:
            self._positions[order.symbol] = BrokerPosition(
                symbol=order.symbol, qty=qty_signed,
                avg_entry_price=fill_price,
                market_value=fill_price * abs(qty_signed),
                unrealized_pnl=0.0,
            )

    async def submit_order(self, symbol, side, qty, order_type="market", limit_price=None):
        oid = str(uuid.uuid4())[:8]
        now = datetime.now(timezone.utc)
        order = BrokerOrder(
            broker_id=oid, symbol=symbol, side=side, qty=qty,
            order_type=order_type, limit_price=limit_price,
            created_at=now, updated_at=now,
        )
        current_price = self._price_for(symbol)
        if order_type == "limit":
            if self._limit_crossed(order, current_price):
                self._execute_fill(order, current_price)
            else:
                order.status = "pending"
                self._orders[oid] = order
            return order
        # Market order: apply liquidity constraint for partial fill simulation
        fill_price = current_price  # slippage handled by runner layer
        vol = self._volumes.get(symbol, 0)
        if vol > 0 and qty > 0:
            max_fillable = max(1, int(vol * self._liquidity_factor))
            actual_fill = min(qty, max_fillable)
        else:
            actual_fill = qty  # No volume data → fill fully (backward compatible)
        self._execute_fill(order, fill_price, fill_qty=actual_fill)
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


class RouterOrderManager:
    """Routes orders to the correct broker based on symbol prefix."""

    def __init__(self, stock_broker, crypto_broker, fallback_broker=None):
        """Initialize router with broker instances.
        
        Args:
            stock_broker: Broker for HK.xxx and US.xxx (e.g. FutuStockBroker)
            crypto_broker: Broker for crypto symbols (e.g. FutuCryptoBroker)
            fallback_broker: Optional fallback (e.g. CryptoBinanceBroker)
        """
        self._stock_broker = stock_broker
        self._crypto_broker = crypto_broker
        self._fallback = fallback_broker

    def _broker_for(self, symbol: str):
        """Determine which broker handles a given symbol."""
        if "/" in symbol:               # "BTC/USDT" — crypto internal format
            return self._crypto_broker
        if symbol.startswith("CRYPTO_"):  # "CRYPTO_BTC" — Binance-style
            if self._fallback is not None:
                return self._fallback
            return self._crypto_broker
        if symbol.startswith("HK.") or symbol.startswith("US."):
            return self._stock_broker
        if symbol.startswith("CC."):
            return self._crypto_broker
        raise ValueError(f"Unknown symbol prefix: {symbol}")

    async def submit_order(self, symbol, side, qty, order_type="market",
                           limit_price=None):
        """Submit order to the appropriate broker."""
        return await self._broker_for(symbol).submit_order(
            symbol, side, qty, order_type=order_type,
            limit_price=limit_price,
        )

    async def cancel_order(self, broker_id: str) -> bool:
        """Cancel an order. Tries all brokers until found."""
        for broker in (self._stock_broker, self._crypto_broker, self._fallback):
            if broker is None:
                continue
            if await broker.cancel_order(broker_id):
                return True
        return False

    async def get_order(self, broker_id: str):
        """Query order. Tries all brokers until found."""
        for broker in (self._stock_broker, self._crypto_broker, self._fallback):
            if broker is None:
                continue
            order = await broker.get_order(broker_id)
            if order is not None:
                return order
        return None

    async def get_positions(self) -> list:
        """Aggregate positions from all brokers."""
        positions = []
        for broker in (self._stock_broker, self._crypto_broker, self._fallback):
            if broker is None:
                continue
            positions.extend(await broker.get_positions())
        return positions

    async def get_account(self):
        """Return stock broker account (primary)."""
        return await self._stock_broker.get_account()

    async def get_open_orders(self) -> list:
        """Aggregate open orders from all brokers."""
        orders = []
        for broker in (self._stock_broker, self._crypto_broker, self._fallback):
            if broker is None:
                continue
            orders.extend(await broker.get_open_orders())
        return orders
