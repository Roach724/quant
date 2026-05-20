"""Unit tests for FutuCryptoBroker — all mock, no real OpenD connection."""

from unittest.mock import MagicMock, patch

import pandas as pd
import pytest
from futu import RET_OK

from oms.broker import BrokerAccount, BrokerOrder, BrokerPosition
from oms.broker.crypto_futu_broker import FutuCryptoBroker


# ---------------------------------------------------------------------------
# Helpers to build mock response DataFrames
# ---------------------------------------------------------------------------

def _mock_acc_list_df(acc_id=12345, trdmarket_auth="CRYPTO"):
    return pd.DataFrame([{
        "acc_id": acc_id,
        "trdmarket_auth": trdmarket_auth,
    }])


def _mock_order_df(order_id="2001", code="CC.BTCUSDT", trd_side="BUY",
                   qty=0.5, dealt_qty=0.0, status="SUBMITTED",
                   dealt_avg_price=0.0):
    return pd.DataFrame([{
        "order_id": order_id,
        "code": code,
        "trd_side": trd_side,
        "qty": qty,
        "dealt_qty": dealt_qty,
        "order_status": status,
        "dealt_avg_price": dealt_avg_price,
    }])


def _mock_position_df(code="CC.BTC", qty=0.1, cost_price=65000.0,
                      market_val=6700.0, unrealized_pl=200.0):
    return pd.DataFrame([{
        "code": code,
        "qty": qty,
        "cost_price": cost_price,
        "market_val": market_val,
        "unrealized_pl": unrealized_pl,
    }])


def _mock_account_df(cash=50000.0, total_asset=56700.0, buy_power=113400.0):
    return pd.DataFrame([{
        "cash": cash,
        "total_asset": total_asset,
        "buy_power": buy_power,
    }])


def _make_mock_ctx(get_acc_list_ret=(RET_OK, _mock_acc_list_df()),
                   place_order_ret=(RET_OK, _mock_order_df()),
                   modify_order_ret=(RET_OK, None),
                   order_list_ret=(RET_OK, _mock_order_df()),
                   position_list_ret=(RET_OK, _mock_position_df()),
                   accinfo_ret=(RET_OK, _mock_account_df())):
    """Build a MagicMock that behaves like OpenCryptoTradeContext."""
    ctx = MagicMock()
    ctx.get_acc_list.return_value = get_acc_list_ret
    ctx.place_order.return_value = place_order_ret
    ctx.modify_order.return_value = modify_order_ret
    ctx.order_list_query.return_value = order_list_ret
    ctx.position_list_query.return_value = position_list_ret
    ctx.accinfo_query.return_value = accinfo_ret
    return ctx


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestToFutuCode:
    def test_btc_usdt(self):
        broker = FutuCryptoBroker()
        assert broker._to_futu_code("BTC/USDT") == "CC.BTCUSDT"

    def test_eth_usdt(self):
        broker = FutuCryptoBroker()
        assert broker._to_futu_code("ETH/USDT") == "CC.ETHUSDT"


class TestResolveAccId:
    def test_resolve_acc_id(self):
        broker = FutuCryptoBroker()
        mock_ctx = _make_mock_ctx(
            get_acc_list_ret=(RET_OK, _mock_acc_list_df(acc_id=12345))
        )
        broker._ctx = mock_ctx

        acc_id = broker._resolve_acc_id()
        assert acc_id == 12345
        mock_ctx.get_acc_list.assert_called_once()

    def test_resolve_acc_id_cached(self):
        broker = FutuCryptoBroker()
        mock_ctx = _make_mock_ctx(
            get_acc_list_ret=(RET_OK, _mock_acc_list_df(acc_id=12345))
        )
        broker._ctx = mock_ctx

        # First call
        acc_id_1 = broker._resolve_acc_id()
        assert acc_id_1 == 12345
        # Second call should use cache, get_acc_list NOT called again
        acc_id_2 = broker._resolve_acc_id()
        assert acc_id_2 == 12345
        mock_ctx.get_acc_list.assert_called_once()

    def test_resolve_acc_id_no_crypto(self):
        broker = FutuCryptoBroker()
        mock_ctx = _make_mock_ctx(
            get_acc_list_ret=(RET_OK, _mock_acc_list_df(
                acc_id=99999, trdmarket_auth="HK"
            ))
        )
        broker._ctx = mock_ctx

        with pytest.raises(RuntimeError, match="No crypto account found"):
            broker._resolve_acc_id()

    def test_resolve_acc_id_failure(self):
        broker = FutuCryptoBroker()
        mock_ctx = _make_mock_ctx(
            get_acc_list_ret=(-1, "Connection error")
        )
        broker._ctx = mock_ctx

        with pytest.raises(RuntimeError, match="Failed to get account list"):
            broker._resolve_acc_id()


class TestSubmitOrder:
    @pytest.mark.asyncio
    async def test_submit_order_market(self):
        broker = FutuCryptoBroker()
        mock_ctx = _make_mock_ctx(
            get_acc_list_ret=(RET_OK, _mock_acc_list_df(acc_id=12345)),
            place_order_ret=(RET_OK, _mock_order_df(
                order_id="2001", code="CC.BTCUSDT", trd_side="BUY", qty=0.5,
            )),
        )
        broker._ctx = mock_ctx

        order = await broker.submit_order("BTC/USDT", "buy", 0.5)
        assert isinstance(order, BrokerOrder)
        assert order.broker_id == "2001"
        assert order.symbol == "BTC/USDT"
        assert order.side == "buy"
        assert order.qty == 0.5
        assert order.status == "submitted"
        assert order.order_type == "market"

        # Verify the SDK was called with correct args, including "CC.BTCUSDT"
        mock_ctx.place_order.assert_called_once_with(
            price=0.0,
            qty=0.5,
            code="CC.BTCUSDT",
            trd_side="BUY",
            order_type="MARKET",
            trd_env="REAL",
            acc_id=12345,
            remark="quant-futu-crypto",
        )

    @pytest.mark.asyncio
    async def test_submit_order_limit(self):
        broker = FutuCryptoBroker()
        mock_ctx = _make_mock_ctx(
            get_acc_list_ret=(RET_OK, _mock_acc_list_df(acc_id=12345)),
            place_order_ret=(RET_OK, _mock_order_df(
                order_id="2002", code="CC.ETHUSDT", trd_side="SELL", qty=1.0,
            )),
        )
        broker._ctx = mock_ctx

        order = await broker.submit_order(
            "ETH/USDT", "sell", 1.0, order_type="limit", limit_price=3500.0
        )
        assert order.order_type == "limit"
        assert order.limit_price == 3500.0
        assert order.side == "sell"
        assert order.symbol == "ETH/USDT"

        mock_ctx.place_order.assert_called_once_with(
            price=3500.0,
            qty=1.0,
            code="CC.ETHUSDT",
            trd_side="SELL",
            order_type="NORMAL",
            trd_env="REAL",
            acc_id=12345,
            remark="quant-futu-crypto",
        )

    @pytest.mark.asyncio
    async def test_submit_order_failure(self):
        broker = FutuCryptoBroker()
        mock_ctx = _make_mock_ctx(
            get_acc_list_ret=(RET_OK, _mock_acc_list_df(acc_id=12345)),
            place_order_ret=(-1, "Insufficient funds"),
        )
        broker._ctx = mock_ctx

        with pytest.raises(RuntimeError, match="Crypto order failed"):
            await broker.submit_order("BTC/USDT", "buy", 0.5)

    @pytest.mark.asyncio
    async def test_submit_order_uses_acc_id_cache(self):
        """submit_order should not re-query acc_list if acc_id already cached."""
        broker = FutuCryptoBroker()
        mock_ctx = _make_mock_ctx(
            get_acc_list_ret=(RET_OK, _mock_acc_list_df(acc_id=12345)),
            place_order_ret=(RET_OK, _mock_order_df()),
        )
        broker._ctx = mock_ctx
        # Pre-warm acc_id cache
        broker._acc_id = 54321

        await broker.submit_order("BTC/USDT", "buy", 0.5)
        # get_acc_list should NOT have been called
        mock_ctx.get_acc_list.assert_not_called()
        # place_order should use cached acc_id
        mock_ctx.place_order.assert_called_once()
        call_kwargs = mock_ctx.place_order.call_args.kwargs
        assert call_kwargs["acc_id"] == 54321


class TestCancelOrder:
    @pytest.mark.asyncio
    async def test_cancel_order_success(self):
        broker = FutuCryptoBroker()
        mock_ctx = _make_mock_ctx(
            get_acc_list_ret=(RET_OK, _mock_acc_list_df(acc_id=12345)),
            modify_order_ret=(RET_OK, None),
        )
        broker._ctx = mock_ctx

        result = await broker.cancel_order("2001")
        assert result is True

        mock_ctx.modify_order.assert_called_once_with(
            modify_order_op="CANCEL",
            order_id="2001",
            qty=0,
            price=0,
            trd_env="REAL",
            acc_id=12345,
        )

    @pytest.mark.asyncio
    async def test_cancel_order_failure(self):
        broker = FutuCryptoBroker()
        mock_ctx = _make_mock_ctx(
            get_acc_list_ret=(RET_OK, _mock_acc_list_df(acc_id=12345)),
            modify_order_ret=(-1, "Order not found"),
        )
        broker._ctx = mock_ctx

        result = await broker.cancel_order("2001")
        assert result is False


class TestGetOrder:
    @pytest.mark.asyncio
    async def test_get_order_found(self):
        broker = FutuCryptoBroker()
        order_df = _mock_order_df(
            order_id="2001", code="CC.BTCUSDT", trd_side="BUY",
            qty=0.5, dealt_qty=0.3, status="PARTIALLY_FILLED",
            dealt_avg_price=67000.0,
        )
        mock_ctx = _make_mock_ctx(
            get_acc_list_ret=(RET_OK, _mock_acc_list_df(acc_id=12345)),
            order_list_ret=(RET_OK, order_df),
        )
        broker._ctx = mock_ctx

        order = await broker.get_order("2001")
        assert order is not None
        assert order.broker_id == "2001"
        # Symbol converted back: "CC.BTCUSDT" → "BTC/USDT"
        assert order.symbol == "BTC/USDT"
        assert order.side == "buy"
        assert order.qty == 0.5
        assert order.filled_qty == 0.3
        assert order.status == "partially_filled"
        assert order.avg_price == 67000.0

    @pytest.mark.asyncio
    async def test_get_order_not_found(self):
        broker = FutuCryptoBroker()
        order_df = _mock_order_df(order_id="9999")  # different ID
        mock_ctx = _make_mock_ctx(
            get_acc_list_ret=(RET_OK, _mock_acc_list_df(acc_id=12345)),
            order_list_ret=(RET_OK, order_df),
        )
        broker._ctx = mock_ctx

        order = await broker.get_order("2001")
        assert order is None

    @pytest.mark.asyncio
    async def test_get_order_api_failure(self):
        broker = FutuCryptoBroker()
        mock_ctx = _make_mock_ctx(
            get_acc_list_ret=(RET_OK, _mock_acc_list_df(acc_id=12345)),
            order_list_ret=(-1, "API error"),
        )
        broker._ctx = mock_ctx

        order = await broker.get_order("2001")
        assert order is None


class TestGetPositions:
    @pytest.mark.asyncio
    async def test_get_positions(self):
        broker = FutuCryptoBroker()
        pos_df = _mock_position_df(
            code="CC.BTC", qty=0.1, cost_price=65000.0,
            market_val=6700.0, unrealized_pl=200.0,
        )
        mock_ctx = _make_mock_ctx(
            get_acc_list_ret=(RET_OK, _mock_acc_list_df(acc_id=12345)),
            position_list_ret=(RET_OK, pos_df),
        )
        broker._ctx = mock_ctx

        positions = await broker.get_positions()
        assert len(positions) == 1
        p = positions[0]
        assert isinstance(p, BrokerPosition)
        # Symbol: "CC.BTC" → "BTC/USDT"
        assert p.symbol == "BTC/USDT"
        assert p.qty == 0.1
        assert p.avg_entry_price == 65000.0
        assert p.market_value == 6700.0
        assert p.unrealized_pnl == 200.0

    @pytest.mark.asyncio
    async def test_get_positions_empty(self):
        broker = FutuCryptoBroker()
        mock_ctx = _make_mock_ctx(
            get_acc_list_ret=(RET_OK, _mock_acc_list_df(acc_id=12345)),
            position_list_ret=(-1, "No positions"),
        )
        broker._ctx = mock_ctx

        positions = await broker.get_positions()
        assert positions == []


class TestGetAccount:
    @pytest.mark.asyncio
    async def test_get_account(self):
        broker = FutuCryptoBroker()
        mock_ctx = _make_mock_ctx(
            get_acc_list_ret=(RET_OK, _mock_acc_list_df(acc_id=12345)),
            accinfo_ret=(RET_OK, _mock_account_df(
                cash=50000.0, total_asset=56700.0, buy_power=113400.0,
            )),
        )
        broker._ctx = mock_ctx

        acc = await broker.get_account()
        assert isinstance(acc, BrokerAccount)
        assert acc.cash == 50000.0
        assert acc.equity == 56700.0
        assert acc.buying_power == 113400.0

    @pytest.mark.asyncio
    async def test_get_account_failure(self):
        broker = FutuCryptoBroker()
        mock_ctx = _make_mock_ctx(
            get_acc_list_ret=(RET_OK, _mock_acc_list_df(acc_id=12345)),
            accinfo_ret=(-1, "Auth error"),
        )
        broker._ctx = mock_ctx

        acc = await broker.get_account()
        assert acc.cash == 0.0
        assert acc.equity == 0.0
        assert acc.buying_power == 0.0


class TestGetOpenOrders:
    @pytest.mark.asyncio
    async def test_get_open_orders(self):
        broker = FutuCryptoBroker()
        order_df = pd.DataFrame([
            {"order_id": "2001", "code": "CC.BTCUSDT", "trd_side": "BUY",
             "qty": 0.5, "dealt_qty": 0.0, "order_status": "SUBMITTED",
             "dealt_avg_price": 0.0},
            {"order_id": "2002", "code": "CC.ETHUSDT", "trd_side": "SELL",
             "qty": 1.0, "dealt_qty": 0.3, "order_status": "PARTIALLY_FILLED",
             "dealt_avg_price": 3500.0},
        ])
        mock_ctx = _make_mock_ctx(
            get_acc_list_ret=(RET_OK, _mock_acc_list_df(acc_id=12345)),
            order_list_ret=(RET_OK, order_df),
        )
        broker._ctx = mock_ctx

        orders = await broker.get_open_orders()
        assert len(orders) == 2
        assert orders[0].broker_id == "2001"
        assert orders[0].symbol == "BTC/USDT"
        assert orders[0].side == "buy"
        assert orders[1].broker_id == "2002"
        assert orders[1].symbol == "ETH/USDT"
        assert orders[1].side == "sell"
        assert orders[1].status == "partially_filled"
        assert orders[1].avg_price == 3500.0

    @pytest.mark.asyncio
    async def test_get_open_orders_empty(self):
        broker = FutuCryptoBroker()
        mock_ctx = _make_mock_ctx(
            get_acc_list_ret=(RET_OK, _mock_acc_list_df(acc_id=12345)),
            order_list_ret=(-1, "No orders"),
        )
        broker._ctx = mock_ctx

        orders = await broker.get_open_orders()
        assert orders == []


class TestClose:
    def test_close(self):
        broker = FutuCryptoBroker()
        mock_ctx = MagicMock()
        broker._ctx = mock_ctx

        broker.close()
        mock_ctx.close.assert_called_once()
        assert broker._ctx is None

    def test_close_no_context(self):
        broker = FutuCryptoBroker()
        # _ctx is None by default
        broker.close()  # should not raise

    def test_close_handles_close_error(self):
        broker = FutuCryptoBroker()
        mock_ctx = MagicMock()
        mock_ctx.close.side_effect = RuntimeError("Connection lost")
        broker._ctx = mock_ctx

        broker.close()  # should not raise
        assert broker._ctx is None
