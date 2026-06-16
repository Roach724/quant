"""Futu OpenD stock trading broker — HK + US equities."""

import logging
import os
from datetime import datetime, timezone
from typing import Optional

import pandas as pd

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

from oms.broker import BrokerAccount, BrokerDeal, BrokerOrder, BrokerPosition

logger = logging.getLogger(__name__)


def _safe_float(val) -> float:
    """Convert to float, handling 'N/A' and None."""
    if val is None:
        return 0.0
    try:
        return float(val)
    except (ValueError, TypeError):
        return 0.0


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

    _lot_cache: dict[str, int] = {}

    @staticmethod
    def _format_symbol(symbol: str, market: str) -> str:
        """Add market prefix to symbol if required by Futu API.

        Futu expects format: US.AAPL / HK.00700 / SZ.000001
        """
        prefix_map = {"hk": "HK.", "us": "US."}
        prefix = prefix_map.get(market)
        if prefix and not symbol.startswith(prefix):
            return f"{prefix}{symbol}"
        return symbol

    @staticmethod
    def _market_from_symbol(symbol: str) -> str | None:
        """Extract Futu market code from symbol prefix.

        US.AAPL → 'US', HK.00700 → 'HK', plain AAPL → None
        """
        if "." in symbol:
            return symbol.split(".")[0]
        return None

    def _ensure_lot(self, symbol: str, qty: int) -> int:
        """Round quantity down to board lot size.

        Query Futu for the stock's lot_size and round down.
        US lot_size=1 (no constraint), HK varies (100/500/...).
        """
        futu_market = self._market_from_symbol(symbol)
        if not futu_market:
            return qty
        # Use raw symbol (without prefix) as cache key
        raw = symbol.split(".", 1)[1] if "." in symbol else symbol
        lot = self._lot_cache.get(raw)
        if lot is None:
            from futu import OpenQuoteContext
            try:
                ctx = OpenQuoteContext(host=self.host, port=self.port)
                ret, data = ctx.get_stock_basicinfo(
                    futu_market, "STOCK", [symbol]
                )
                if ret == 0 and data is not None and len(data) > 0:
                    lot = int(data.iloc[0]["lot_size"])
                    self._lot_cache[raw] = lot
                    logger.info("Lot size for %s = %d", symbol, lot)
                else:
                    lot = 1
                    logger.warning(
                        "Lot size query failed for %s, default 1", symbol
                    )
                ctx.close()
            except Exception as e:
                lot = 1
                logger.warning(
                    "Lot size query error for %s: %s, default 1", symbol, e
                )
        return (qty // lot) * lot

    async def submit_order(
        self,
        symbol: str,
        side: str,
        qty: float,
        order_type: str = "market",
        limit_price: Optional[float] = None,
    ) -> BrokerOrder | None:
        """Submit a market or limit order. Returns None if qty too small for one lot."""
        symbol = self._format_symbol(symbol, self.market)
        qty = self._ensure_lot(symbol, int(qty))
        if qty <= 0:
            logger.warning(
                "Insufficient cash for one lot of %s (lot_size needed)",
                symbol,
            )
            return None
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
                    unrealized_pnl=_safe_float(row.get("unrealized_pl", 0)),
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
            cash=_safe_float(row["cash"]),
            equity=_safe_float(row.get("total_assets", 0)),
            buying_power=_safe_float(row.get("power", 0)),
        )

    async def get_open_orders(self) -> list[BrokerOrder]:
        """Get only open orders (submitted, pending, partially filled)."""
        from futu import OrderStatus
        ctx = self._get_ctx()
        ret, data = ctx.order_list_query(
            trd_env=self.trd_env,
            status_filter_list=[
                OrderStatus.SUBMITTED,
                OrderStatus.FILLED_PART,
            ],
        )
        if ret != RET_OK:
            return []
        orders = []
        for _, row in data.iterrows():
            orders.append(
                BrokerOrder(
                    broker_id=str(row["order_id"]),
                    symbol=str(row["code"]),
                    side="buy" if row["trd_side"] == "BUY" else "sell",
                    qty=_safe_float(row["qty"]),
                    filled_qty=_safe_float(row.get("dealt_qty", 0)),
                    status=str(row.get("order_status", "unknown")).lower(),
                    avg_price=_safe_float(row.get("dealt_avg_price", 0))
                    if row.get("dealt_avg_price")
                    else None,
                )
            )
        return orders

    async def get_all_orders(self) -> list[BrokerOrder]:
        """Get ALL historical orders (all statuses, unfiltered)."""
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
                    qty=_safe_float(row["qty"]),
                    filled_qty=_safe_float(row.get("dealt_qty", 0)),
                    status=str(row.get("order_status", "unknown")).lower(),
                    avg_price=_safe_float(row.get("dealt_avg_price", 0))
                    if row.get("dealt_avg_price")
                    else None,
                )
            )
        return orders

    async def get_deals(self) -> list[BrokerDeal]:
        """Get deal/fill list (成交明细)."""
        ctx = self._get_ctx()
        ret, data = ctx.deal_list_query(trd_env=self.trd_env)
        if ret != RET_OK:
            return []
        deals = []
        for _, row in data.iterrows():
            deals.append(
                BrokerDeal(
                    deal_id=str(row.get("deal_id", "")),
                    order_id=str(row.get("order_id", "")),
                    symbol=str(row["code"]),
                    side="buy" if str(row["trd_side"]).upper() == "BUY" else "sell",
                    qty=_safe_float(row["qty"]),
                    price=_safe_float(row["price"]),
                    created_at=pd.to_datetime(row["create_time"]).to_pydatetime()
                    if "create_time" in row and pd.notna(row["create_time"])
                    else datetime.now(timezone.utc),
                )
            )
        return deals

    def close(self):
        """Close the trade context."""
        if self._ctx is not None:
            try:
                self._ctx.close()
            except Exception:
                pass
            self._ctx = None
