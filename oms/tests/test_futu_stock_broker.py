"""Unit tests for FutuStockBroker — all mock, no real OpenD connection."""

from unittest.mock import MagicMock, patch

import pandas as pd
import pytest
from futu import RET_OK

from oms.broker import BrokerAccount, BrokerOrder, BrokerPosition
from oms.broker.futu_stock_broker import FutuStockBroker


# ---------------------------------------------------------------------------
# Helpers to build mock response DataFrames
# ---------------------------------------------------------------------------

def _mock_order_df(order_id="1001", code="HK.00700", trd_side="BUY",
                   qty=100, dealt_qty=0, status="SUBMITTED",
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


def _mock_position_df(code="HK.00700", qty=100, cost_price=320.0,
                      market_val=33000.0, unrealized_pl=1000.0):
    return pd.DataFrame([{
        "code": code,
        "qty": qty,
        "cost_price": cost_price,
        "market_val": market_val,
        "unrealized_pl": unrealized_pl,
    }])


def _mock_account_df(cash=50000.0, total_asset=83000.0, buy_power=166000.0):
    return pd.DataFrame([{
        "cash": cash,
        "total_asset": total_asset,
        "buy_power": buy_power,
    }])


def _make_mock_ctx(place_order_ret=(RET_OK, _mock_order_df()),
                   modify_order_ret=(RET_OK, None),
                   order_list_ret=(RET_OK, _mock_order_df()),
                   position_list_ret=(RET_OK, _mock_position_df()),
                   accinfo_ret=(RET_OK, _mock_account_df())):
    """Build a MagicMock that behaves like OpenSecTradeContext."""
    ctx = MagicMock()
    ctx.place_order.return_value = place_order_ret
    ctx.modify_order.return_value = modify_order_ret
    ctx.order_list_query.return_value = order_list_ret
    ctx.position_list_query.return_value = position_list_ret
    ctx.accinfo_query.return_value = accinfo_ret
    return ctx


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_submit_order_market():
    broker = FutuStockBroker()
    mock_ctx = _make_mock_ctx()
    broker._ctx = mock_ctx

    order = await broker.submit_order("HK.00700", "buy", 100)
    assert isinstance(order, BrokerOrder)
    assert order.broker_id == "1001"
    assert order.symbol == "HK.00700"
    assert order.side == "buy"
    assert order.qty == 100
    assert order.status == "submitted"
    assert order.order_type == "market"

    # Verify the SDK was called with correct args
    mock_ctx.place_order.assert_called_once_with(
        price=0.0,
        qty=100,
        code="HK.00700",
        trd_side="BUY",
        order_type="MARKET",
        trd_env="SIMULATE",
    )


@pytest.mark.asyncio
async def test_submit_order_limit():
    broker = FutuStockBroker()
    mock_ctx = _make_mock_ctx()
    broker._ctx = mock_ctx

    order = await broker.submit_order(
        "HK.00700", "sell", 200, order_type="limit", limit_price=350.0
    )
    assert order.order_type == "limit"
    assert order.limit_price == 350.0
    assert order.side == "sell"

    mock_ctx.place_order.assert_called_once_with(
        price=350.0,
        qty=200,
        code="HK.00700",
        trd_side="SELL",
        order_type="NORMAL",
        trd_env="SIMULATE",
    )


@pytest.mark.asyncio
async def test_submit_order_failure():
    broker = FutuStockBroker()
    mock_ctx = _make_mock_ctx(place_order_ret=(-1, "Insufficient funds"))
    broker._ctx = mock_ctx

    with pytest.raises(RuntimeError, match="Order failed"):
        await broker.submit_order("HK.00700", "buy", 100)


@pytest.mark.asyncio
async def test_cancel_order_success():
    broker = FutuStockBroker()
    mock_ctx = _make_mock_ctx(modify_order_ret=(RET_OK, None))
    broker._ctx = mock_ctx

    result = await broker.cancel_order("1001")
    assert result is True

    mock_ctx.modify_order.assert_called_once_with(
        modify_order_op="CANCEL",
        order_id="1001",
        qty=0,
        price=0,
        trd_env="SIMULATE",
    )


@pytest.mark.asyncio
async def test_cancel_order_failure():
    broker = FutuStockBroker()
    mock_ctx = _make_mock_ctx(modify_order_ret=(-1, "Order not found"))
    broker._ctx = mock_ctx

    result = await broker.cancel_order("1001")
    assert result is False


@pytest.mark.asyncio
async def test_get_order_found():
    broker = FutuStockBroker()
    order_df = _mock_order_df(
        order_id="1001", code="HK.00700", trd_side="BUY",
        qty=100, dealt_qty=50, status="PARTIALLY_FILLED",
        dealt_avg_price=325.0,
    )
    mock_ctx = _make_mock_ctx(order_list_ret=(RET_OK, order_df))
    broker._ctx = mock_ctx

    order = await broker.get_order("1001")
    assert order is not None
    assert order.broker_id == "1001"
    assert order.symbol == "HK.00700"
    assert order.side == "buy"
    assert order.qty == 100
    assert order.filled_qty == 50
    assert order.status == "partially_filled"
    assert order.avg_price == 325.0


@pytest.mark.asyncio
async def test_get_order_not_found():
    broker = FutuStockBroker()
    order_df = _mock_order_df(order_id="9999")  # different ID
    mock_ctx = _make_mock_ctx(order_list_ret=(RET_OK, order_df))
    broker._ctx = mock_ctx

    order = await broker.get_order("1001")
    assert order is None


@pytest.mark.asyncio
async def test_get_order_api_failure():
    broker = FutuStockBroker()
    mock_ctx = _make_mock_ctx(order_list_ret=(-1, "API error"))
    broker._ctx = mock_ctx

    order = await broker.get_order("1001")
    assert order is None


@pytest.mark.asyncio
async def test_get_positions():
    broker = FutuStockBroker()
    pos_df = _mock_position_df(
        code="HK.00700", qty=100, cost_price=320.0,
        market_val=33000.0, unrealized_pl=1000.0,
    )
    mock_ctx = _make_mock_ctx(position_list_ret=(RET_OK, pos_df))
    broker._ctx = mock_ctx

    positions = await broker.get_positions()
    assert len(positions) == 1
    p = positions[0]
    assert isinstance(p, BrokerPosition)
    assert p.symbol == "HK.00700"
    assert p.qty == 100
    assert p.avg_entry_price == 320.0
    assert p.market_value == 33000.0
    assert p.unrealized_pnl == 1000.0


@pytest.mark.asyncio
async def test_get_positions_empty():
    broker = FutuStockBroker()
    mock_ctx = _make_mock_ctx(position_list_ret=(-1, "No positions"))
    broker._ctx = mock_ctx

    positions = await broker.get_positions()
    assert positions == []


@pytest.mark.asyncio
async def test_get_account():
    broker = FutuStockBroker()
    mock_ctx = _make_mock_ctx(
        accinfo_ret=(RET_OK, _mock_account_df(
            cash=50000.0, total_asset=83000.0, buy_power=166000.0,
        ))
    )
    broker._ctx = mock_ctx

    acc = await broker.get_account()
    assert isinstance(acc, BrokerAccount)
    assert acc.cash == 50000.0
    assert acc.equity == 83000.0
    assert acc.buying_power == 166000.0


@pytest.mark.asyncio
async def test_get_account_failure():
    broker = FutuStockBroker()
    mock_ctx = _make_mock_ctx(accinfo_ret=(-1, "Auth error"))
    broker._ctx = mock_ctx

    acc = await broker.get_account()
    assert acc.cash == 0.0
    assert acc.equity == 0.0
    assert acc.buying_power == 0.0


@pytest.mark.asyncio
async def test_get_open_orders():
    broker = FutuStockBroker()
    order_df = pd.DataFrame([
        {"order_id": "1001", "code": "HK.00700", "trd_side": "BUY",
         "qty": 100, "dealt_qty": 0, "order_status": "SUBMITTED",
         "dealt_avg_price": 0.0},
        {"order_id": "1002", "code": "US.AAPL", "trd_side": "SELL",
         "qty": 50, "dealt_qty": 10, "order_status": "PARTIALLY_FILLED",
         "dealt_avg_price": 180.0},
    ])
    mock_ctx = _make_mock_ctx(order_list_ret=(RET_OK, order_df))
    broker._ctx = mock_ctx

    orders = await broker.get_open_orders()
    assert len(orders) == 2
    assert orders[0].broker_id == "1001"
    assert orders[0].symbol == "HK.00700"
    assert orders[0].side == "buy"
    assert orders[1].broker_id == "1002"
    assert orders[1].symbol == "US.AAPL"
    assert orders[1].side == "sell"
    assert orders[1].status == "partially_filled"
    assert orders[1].avg_price == 180.0


@pytest.mark.asyncio
async def test_get_open_orders_empty():
    broker = FutuStockBroker()
    mock_ctx = _make_mock_ctx(order_list_ret=(-1, "No orders"))
    broker._ctx = mock_ctx

    orders = await broker.get_open_orders()
    assert orders == []


def test_close():
    broker = FutuStockBroker()
    mock_ctx = MagicMock()
    broker._ctx = mock_ctx

    broker.close()
    mock_ctx.close.assert_called_once()
    assert broker._ctx is None


def test_close_no_context():
    broker = FutuStockBroker()
    # _ctx is None by default
    broker.close()  # should not raise


def test_close_handles_close_error():
    broker = FutuStockBroker()
    mock_ctx = MagicMock()
    mock_ctx.close.side_effect = RuntimeError("Connection lost")
    broker._ctx = mock_ctx

    broker.close()  # should not raise
    assert broker._ctx is None
