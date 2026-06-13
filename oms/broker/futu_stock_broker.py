"""Futu OpenD stock trading broker — HK + US equities."""

import logging
import os
from typing import Optional

from futu import (
    RET_OK,
    ModifyOrderOp,
    OpenSecTradeContext,
    OrderType,
    SecurityFirm,
    TrdEnv,
    TrdMarket,
    TrdSide,
)

from oms.broker import BrokerAccount, BrokerOrder, BrokerPosition

logger = logging.getLogger(__name__)


class FutuStockBroker:
    """Futu OpenD stock trading broker.

    Uses OpenSecTradeContext for trade operations.
    Default: TrdEnv.SIMULATE (paper trading).
    Switch to TrdEnv.REAL for live trading (requires OpenD GUI unlock).
    """

    def __init__(
        self,
        host: Optional[str] = None,
        port: Optional[int] = None,
        trd_env: TrdEnv = TrdEnv.SIMULATE,
        security_firm: SecurityFirm = SecurityFirm.FUTUSECURITIES,
        market: str = "hk",
    ):
        self.host = host or os.environ.get("OPEND_HOST", "127.0.0.1")
        self.port = port or int(os.environ.get("OPEND_PORT", "11111"))
        self.trd_env = trd_env
        self.security_firm = security_firm
        self.market = market
        self._ctx: Optional[OpenSecTradeContext] = None

    def _get_ctx(self) -> OpenSecTradeContext:
        if self._ctx is None:
            trd_market = TrdMarket.HK if self.market == "hk" else TrdMarket.US
            self._ctx = OpenSecTradeContext(
                filter_trdmarket=trd_market,
                host=self.host,
                port=self.port,
                security_firm=self.security_firm,
            )
        return self._ctx

    async def submit_order(
        self,
        symbol: str,
        side: str,
        qty: float,
        order_type: str = "market",
        limit_price: Optional[float] = None,
    ) -> BrokerOrder:
        """Submit a market or limit order."""
        ctx = self._get_ctx()
        futu_side = TrdSide.BUY if side == "buy" else TrdSide.SELL
        futu_order_type = (
            OrderType.NORMAL if order_type == "limit" else OrderType.MARKET
        )
        price = limit_price if limit_price is not None else 0.0

        ret, data = ctx.place_order(
            price=price,
            qty=qty,
            code=symbol,
            trd_side=futu_side,
            order_type=futu_order_type,
            trd_env=self.trd_env,
        )
        if ret != RET_OK:
            raise RuntimeError(f"Order failed: {data}")

        return BrokerOrder(
            broker_id=str(data["order_id"].iloc[0]),
            symbol=symbol,
            side=side,
            qty=qty,
            status="submitted",
            order_type=order_type,
            limit_price=limit_price,
        )

    async def cancel_order(self, broker_id: str) -> bool:
        """Cancel an existing order."""
        ctx = self._get_ctx()
        ret, data = ctx.modify_order(
            modify_order_op=ModifyOrderOp.CANCEL,
            order_id=broker_id,
            qty=0,
            price=0,
            trd_env=self.trd_env,
        )
        return ret == RET_OK

    async def get_order(self, broker_id: str) -> Optional[BrokerOrder]:
        """Query a specific order by ID."""
        ctx = self._get_ctx()
        ret, data = ctx.order_list_query(trd_env=self.trd_env)
        if ret != RET_OK:
            return None
        match = data[data["order_id"].astype(str) == str(broker_id)]
        if match.empty:
            return None
        row = match.iloc[0]
        return BrokerOrder(
            broker_id=str(row["order_id"]),
            symbol=str(row["code"]),
            side="buy" if row["trd_side"] == "BUY" else "sell",
            qty=float(row["qty"]),
            filled_qty=float(row.get("dealt_qty", 0)),
            status=str(row.get("order_status", "unknown")).lower(),
            avg_price=float(row.get("dealt_avg_price", 0))
            if row.get("dealt_avg_price")
            else None,
        )

    async def get_positions(self) -> list[BrokerPosition]:
        """Get all current positions."""
        ctx = self._get_ctx()
        ret, data = ctx.position_list_query(trd_env=self.trd_env)
        if ret != RET_OK:
            return []
        positions = []
        for _, row in data.iterrows():
            positions.append(
                BrokerPosition(
                    symbol=str(row["code"]),
                    qty=float(row["qty"]),
                    avg_entry_price=float(row["cost_price"]),
                    market_value=float(row["market_val"]),
                    unrealized_pnl=float(row.get("unrealized_pl", 0)),
                )
            )
        return positions

    async def get_account(self) -> BrokerAccount:
        """Get account information (cash, equity, buying power)."""
        ctx = self._get_ctx()
        ret, data = ctx.accinfo_query(trd_env=self.trd_env)
        if ret != RET_OK:
            return BrokerAccount(cash=0.0, equity=0.0, buying_power=0.0)
        row = data.iloc[0]
        return BrokerAccount(
            cash=float(row["cash"]),
            equity=float(row.get("total_assets", 0)),
            buying_power=float(row.get("power", 0)),
        )

    async def get_open_orders(self) -> list[BrokerOrder]:
        """Get all submitted/pending orders."""
        ctx = self._get_ctx()
        ret, data = ctx.order_list_query(trd_env=self.trd_env)
        if ret != RET_OK:
            return []
        orders = []
        for _, row in data.iterrows():
            orders.append(
                BrokerOrder(
                    broker_id=str(row["order_id"]),
                    symbol=str(row["code"]),
                    side="buy" if row["trd_side"] == "BUY" else "sell",
                    qty=float(row["qty"]),
                    filled_qty=float(row.get("dealt_qty", 0)),
                    status=str(row.get("order_status", "unknown")).lower(),
                    avg_price=float(row.get("dealt_avg_price", 0))
                    if row.get("dealt_avg_price")
                    else None,
                )
            )
        return orders

    def close(self):
        """Close the trade context."""
        if self._ctx is not None:
            try:
                self._ctx.close()
            except Exception:
                pass
            self._ctx = None
