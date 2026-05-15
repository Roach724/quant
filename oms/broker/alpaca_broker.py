import os
from oms.broker import BrokerOrder, BrokerPosition, BrokerAccount


class AlpacaBroker:
    def __init__(self, api_key=None, api_secret=None, paper=True):
        key = api_key or os.environ.get("ALPACA_API_KEY")
        secret = api_secret or os.environ.get("ALPACA_API_SECRET")
        if not key or not secret:
            raise ValueError("Alpaca API credentials required")
        self._client: "TradingClient" = None
        self._api_key = key
        self._api_secret = secret
        self._paper = paper
        self._init_client()

    def _init_client(self):
        from alpaca.trading.client import TradingClient
        self._client = TradingClient(self._api_key, self._api_secret, paper=self._paper)

    async def submit_order(self, symbol, side, qty, order_type="market", limit_price=None):
        from alpaca.trading.requests import MarketOrderRequest, LimitOrderRequest
        from alpaca.trading.enums import OrderSide, TimeInForce
        side_enum = OrderSide.BUY if side == "buy" else OrderSide.SELL
        if order_type == "limit" and limit_price:
            req = LimitOrderRequest(
                symbol=symbol, qty=qty, side=side_enum,
                limit_price=limit_price, time_in_force=TimeInForce.DAY,
            )
        else:
            req = MarketOrderRequest(
                symbol=symbol, qty=qty, side=side_enum,
                time_in_force=TimeInForce.DAY,
            )
        resp = self._client.submit_order(req)
        return BrokerOrder(
            broker_id=str(resp.id), symbol=resp.symbol, side=side,
            qty=int(float(resp.qty or 0)),
            filled_qty=int(float(resp.filled_qty or 0)),
            status=resp.status,
            avg_price=float(resp.filled_avg_price) if resp.filled_avg_price else None,
            created_at=resp.created_at, updated_at=resp.updated_at,
        )

    async def cancel_order(self, broker_id):
        try:
            self._client.cancel_order_by_id(broker_id)
            return True
        except Exception:
            return False

    async def get_order(self, broker_id):
        resp = self._client.get_order_by_id(broker_id)
        return BrokerOrder(
            broker_id=str(resp.id), symbol=resp.symbol, side=resp.side,
            qty=int(float(resp.qty or 0)),
            filled_qty=int(float(resp.filled_qty or 0)),
            status=resp.status,
            avg_price=float(resp.filled_avg_price) if resp.filled_avg_price else None,
        )

    async def get_positions(self):
        positions = self._client.get_all_positions()
        return [
            BrokerPosition(
                symbol=p.symbol, qty=int(float(p.qty)),
                avg_entry_price=float(p.avg_entry_price),
                market_value=float(p.market_value or 0),
                unrealized_pnl=float(p.unrealized_pl or 0),
            )
            for p in positions
        ]

    async def get_account(self):
        acc = self._client.get_account()
        return BrokerAccount(
            cash=float(acc.cash), equity=float(acc.equity),
            buying_power=float(acc.buying_power or 0),
        )

    async def get_open_orders(self):
        orders = self._client.get_orders()
        return [
            BrokerOrder(
                broker_id=str(o.id), symbol=o.symbol, side=o.side.value,
                qty=int(float(o.qty or 0)),
                filled_qty=int(float(o.filled_qty or 0)),
                status=o.status,
            )
            for o in orders if hasattr(o, "id")
        ]
