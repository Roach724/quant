"""Futu OpenD cryptocurrency trading broker — REAL only (no SIMULATE)."""

import os
import logging
from typing import Optional

from futu import (
    OpenCryptoTradeContext, RET_OK,
    TrdEnv, TrdSide, OrderType, ModifyOrderOp,
)

from oms.broker import BrokerOrder, BrokerPosition, BrokerAccount

logger = logging.getLogger(__name__)


class FutuCryptoBroker:
    """Futu OpenD cryptocurrency trading broker.

    Uses OpenCryptoTradeContext. ONLY TrdEnv.REAL is supported
    (Futu crypto does not offer a simulated trading environment).
    """

    def __init__(
        self,
        host: Optional[str] = None,
        port: Optional[int] = None,
    ):
        self.host = host or os.environ.get("OPEND_HOST", "127.0.0.1")
        self.port = port or int(os.environ.get("OPEND_PORT", "11111"))
        self._ctx: Optional[OpenCryptoTradeContext] = None
        self._acc_id: Optional[int] = None

    def _get_ctx(self) -> OpenCryptoTradeContext:
        if self._ctx is None:
            self._ctx = OpenCryptoTradeContext(
                host=self.host, port=self.port,
            )
        return self._ctx

    def _to_futu_code(self, symbol: str) -> str:
        """Convert 'BTC/USDT' → 'CC.BTCUSDT'."""
        return "CC." + symbol.replace("/", "")

    def _resolve_acc_id(self) -> int:
        """Auto-discover the crypto account ID."""
        if self._acc_id is not None:
            return self._acc_id
        ctx = self._get_ctx()
        ret, data = ctx.get_acc_list()
        if ret != RET_OK:
            raise RuntimeError(f"Failed to get account list: {data}")
        for _, row in data.iterrows():
            trd_auth = str(row.get("trdmarket_auth", ""))
            if "CRYPTO" in trd_auth:
                self._acc_id = int(row["acc_id"])
                return self._acc_id
        raise RuntimeError("No crypto account found")

    async def submit_order(
        self,
        symbol: str,
        side: str,
        qty: float,
        order_type: str = "market",
        limit_price: Optional[float] = None,
    ) -> BrokerOrder:
        """Submit a market or limit order (REAL only)."""
        acc_id = self._resolve_acc_id()
        ctx = self._get_ctx()
        futu_side = TrdSide.BUY if side == "buy" else TrdSide.SELL
        futu_order_type = (
            OrderType.NORMAL if order_type == "limit" else OrderType.MARKET
        )
        price = limit_price if limit_price is not None else 0.0

        ret, data = ctx.place_order(
            price=price,
            qty=qty,
            code=self._to_futu_code(symbol),
            trd_side=futu_side,
            order_type=futu_order_type,
            trd_env=TrdEnv.REAL,
            acc_id=acc_id,
            remark="quant-futu-crypto",
        )
        if ret != RET_OK:
            raise RuntimeError(f"Crypto order failed: {data}")

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
        """Cancel an existing crypto order."""
        acc_id = self._resolve_acc_id()
        ctx = self._get_ctx()
        ret, data = ctx.modify_order(
            modify_order_op=ModifyOrderOp.CANCEL,
            order_id=broker_id,
            qty=0,
            price=0,
            trd_env=TrdEnv.REAL,
            acc_id=acc_id,
        )
        return ret == RET_OK

    async def get_order(self, broker_id: str) -> Optional[BrokerOrder]:
        """Query a specific crypto order by ID."""
        acc_id = self._resolve_acc_id()
        ctx = self._get_ctx()
        ret, data = ctx.order_list_query(
            trd_env=TrdEnv.REAL, acc_id=acc_id,
        )
        if ret != RET_OK:
            return None
        match = data[data["order_id"].astype(str) == str(broker_id)]
        if match.empty:
            return None
        row = match.iloc[0]
        code = str(row["code"])  # "CC.BTC" or "CC.BTCUSDT"
        # Convert back to internal format: "CC.BTCUSDT" → "BTC/USDT"
        internal_sym = code.replace("CC.", "").replace("USDT", "/USDT")
        return BrokerOrder(
            broker_id=str(row["order_id"]),
            symbol=internal_sym,
            side="buy" if row["trd_side"] == "BUY" else "sell",
            qty=float(row["qty"]),
            filled_qty=float(row.get("dealt_qty", 0)),
            status=str(row.get("order_status", "unknown")).lower(),
            avg_price=float(row.get("dealt_avg_price", 0)) if row.get("dealt_avg_price") else None,
        )

    async def get_positions(self) -> list[BrokerPosition]:
        """Get all crypto positions."""
        acc_id = self._resolve_acc_id()
        ctx = self._get_ctx()
        ret, data = ctx.position_list_query(
            trd_env=TrdEnv.REAL, acc_id=acc_id,
        )
        if ret != RET_OK:
            return []
        positions = []
        for _, row in data.iterrows():
            code = str(row["code"])  # "CC.BTC"
            internal_sym = code.replace("CC.", "") + "/USDT"
            positions.append(BrokerPosition(
                symbol=internal_sym,
                qty=float(row["qty"]),
                avg_entry_price=float(row["cost_price"]),
                market_value=float(row["market_val"]),
                unrealized_pnl=float(row.get("unrealized_pl", 0)),
            ))
        return positions

    async def get_account(self) -> BrokerAccount:
        """Get crypto account info."""
        acc_id = self._resolve_acc_id()
        ctx = self._get_ctx()
        ret, data = ctx.accinfo_query(
            trd_env=TrdEnv.REAL, acc_id=acc_id,
        )
        if ret != RET_OK:
            return BrokerAccount(cash=0.0, equity=0.0, buying_power=0.0)
        row = data.iloc[0]
        return BrokerAccount(
            cash=float(row["cash"]),
            equity=float(row["total_asset"]),
            buying_power=float(row.get("buy_power", 0)),
        )

    async def get_open_orders(self) -> list[BrokerOrder]:
        """Get all open crypto orders."""
        acc_id = self._resolve_acc_id()
        ctx = self._get_ctx()
        ret, data = ctx.order_list_query(
            trd_env=TrdEnv.REAL, acc_id=acc_id,
        )
        if ret != RET_OK:
            return []
        orders = []
        for _, row in data.iterrows():
            code = str(row["code"])
            internal_sym = code.replace("CC.", "").replace("USDT", "/USDT")
            orders.append(BrokerOrder(
                broker_id=str(row["order_id"]),
                symbol=internal_sym,
                side="buy" if row["trd_side"] == "BUY" else "sell",
                qty=float(row["qty"]),
                filled_qty=float(row.get("dealt_qty", 0)),
                status=str(row.get("order_status", "unknown")).lower(),
                avg_price=float(row.get("dealt_avg_price", 0)) if row.get("dealt_avg_price") else None,
            ))
        return orders

    def close(self):
        """Close the trade context."""
        if self._ctx is not None:
            try:
                self._ctx.close()
            except Exception:
                pass
            self._ctx = None
