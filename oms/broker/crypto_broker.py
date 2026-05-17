"""Crypto broker implementations: CryptoPaperBroker and CryptoBinanceBroker."""
import os
import random
import uuid
from datetime import UTC, datetime

import ccxt

from oms.broker import BrokerAccount, BrokerOrder, BrokerPosition


class CryptoPaperBroker:
    """Simulated crypto broker for backtesting. Deterministic fills at synthetic prices."""

    def __init__(self, initial_capital: float = 10_000.0):
        self.cash = initial_capital
        self._positions: dict[str, BrokerPosition] = {}
        self._orders: dict[str, BrokerOrder] = {}
        self._prices: dict[str, float] = {}

    def update_price(self, symbol: str, price: float):
        """Set the current market price for a symbol."""
        self._prices[symbol] = price
        # Fill pending limit orders whose price is crossed
        for oid, order in list(self._orders.items()):
            if order.status == "pending" and order.symbol == symbol:
                if self._limit_crossed(order, price):
                    self._execute_fill(order, price)

    def _price_for(self, symbol: str) -> float:
        return self._prices.get(symbol, 100.0)

    def _limit_crossed(self, order: BrokerOrder, current_price: float) -> bool:
        if order.limit_price is None:
            return False
        if order.side == "buy":
            return current_price <= order.limit_price
        return current_price >= order.limit_price

    def _execute_fill(self, order: BrokerOrder, fill_price: float):
        cost = fill_price * order.qty
        self.cash -= cost
        order.status = "filled"
        order.filled_qty = order.qty
        order.avg_price = fill_price
        order.updated_at = datetime.now(UTC)
        pos = self._positions.get(order.symbol)
        if pos:
            total_qty = pos.qty + (order.qty if order.side == "buy" else -order.qty)
            if total_qty > 0:
                pos.avg_entry_price = (
                    (pos.avg_entry_price * pos.qty) + (fill_price * order.qty)
                ) / (pos.qty + order.qty)
            pos.qty = total_qty
        else:
            qty_signed = order.qty if order.side == "buy" else -order.qty
            self._positions[order.symbol] = BrokerPosition(
                symbol=order.symbol, qty=qty_signed,
                avg_entry_price=fill_price,
                market_value=fill_price * abs(qty_signed),
                unrealized_pnl=0.0,
            )

    async def submit_order(self, symbol, side, qty, order_type="market", limit_price=None):
        oid = str(uuid.uuid4())[:8]
        now = datetime.now(UTC)
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
        # Market order: fill immediately with small random slippage
        fill_price = current_price + random.uniform(-0.5, 0.5)
        self._execute_fill(order, fill_price)
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


class CryptoBinanceBroker:
    """Real Binance broker via ccxt. Supports testnet and live futures trading."""

    def __init__(self, api_key=None, api_secret=None, testnet=True):
        key = api_key or os.environ.get("BINANCE_API_KEY", "")
        secret = api_secret or os.environ.get("BINANCE_API_SECRET", "")
        if not key or not secret:
            raise ValueError(
                "Binance API credentials required. "
                "Set BINANCE_API_KEY and BINANCE_API_SECRET."
            )
        config = {
            "apiKey": key,
            "secret": secret,
            "enableRateLimit": True,
        }
        if testnet:
            config["urls"] = {
                "api": {
                    "public": "https://testnet.binancefuture.com/fapi/v1",
                    "private": "https://testnet.binancefuture.com/fapi/v1",
                }
            }
        self._exchange = ccxt.binance(config)

    async def submit_order(self, symbol, side, qty, order_type="market", limit_price=None):
        try:
            params = {}
            if order_type == "limit" and limit_price:
                resp = self._exchange.create_limit_order(
                    symbol, side, qty, limit_price, params
                )
            else:
                resp = self._exchange.create_market_order(
                    symbol, side, qty, params
                )
            return BrokerOrder(
                broker_id=str(resp.get("id", "")),
                symbol=resp.get("symbol", symbol),
                side=side,
                qty=qty,
                filled_qty=float(resp.get("filled", 0)),
                status=resp.get("status", "unknown"),
                avg_price=float(resp.get("average", 0)) if resp.get("average") else None,
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
        except Exception as e:
            raise RuntimeError(f"Binance order failed: {e}")

    async def cancel_order(self, broker_id):
        try:
            self._exchange.cancel_order(broker_id)
            return True
        except Exception:
            return False

    async def get_order(self, broker_id):
        resp = self._exchange.fetch_order(broker_id)
        return BrokerOrder(
            broker_id=str(resp.get("id", broker_id)),
            symbol=resp.get("symbol", ""),
            side=resp.get("side", ""),
            qty=float(resp.get("amount", 0)),
            filled_qty=float(resp.get("filled", 0)),
            status=resp.get("status", "unknown"),
        )

    async def get_positions(self):
        try:
            positions = self._exchange.fetch_positions()
        except Exception:
            positions = []
        result = []
        for p in positions:
            qty = float(p.get("contracts", 0))
            if qty == 0:
                continue
            result.append(BrokerPosition(
                symbol=p.get("symbol", ""),
                qty=qty,
                avg_entry_price=float(p.get("entryPrice", 0)),
                market_value=float(p.get("notional", 0)),
                unrealized_pnl=float(p.get("unrealizedPnl", 0)),
            ))
        return result

    async def get_account(self):
        balance = self._exchange.fetch_balance()
        usdt = balance.get("USDT", {})
        cash = float(usdt.get("free", 0))
        total = float(balance.get("total", {}).get("USDT", cash))
        positions = await self.get_positions()
        return BrokerAccount(
            cash=cash,
            equity=total + sum(p.unrealized_pnl for p in positions),
            buying_power=total * 2,
            positions=positions,
        )

    async def get_open_orders(self):
        orders = self._exchange.fetch_open_orders()
        return [
            BrokerOrder(
                broker_id=str(o.get("id", "")),
                symbol=o.get("symbol", ""),
                side=o.get("side", ""),
                qty=float(o.get("amount", 0)),
                filled_qty=float(o.get("filled", 0)),
                status=o.get("status", "open"),
            )
            for o in orders
        ]
