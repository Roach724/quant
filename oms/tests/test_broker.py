import pytest
from oms.broker import PaperBroker


@pytest.mark.asyncio
async def test_paper_broker_submit_market_order():
    broker = PaperBroker(initial_capital=100_000.0)
    order = await broker.submit_order("AAPL", "buy", 10)
    assert order.broker_id is not None
    assert order.symbol == "AAPL"
    assert order.side == "buy"
    assert order.qty == 10
    assert order.status == "filled"
    assert order.filled_qty == 10
    assert order.avg_price is not None


@pytest.mark.asyncio
async def test_paper_broker_submit_limit_order():
    broker = PaperBroker(initial_capital=100_000.0)
    order = await broker.submit_order("AAPL", "buy", 10, order_type="limit", limit_price=150.0)
    assert order.status == "pending"
    assert order.order_type == "limit"
    assert order.limit_price == 150.0
    open_orders = await broker.get_open_orders()
    assert len(open_orders) == 1
    assert open_orders[0].broker_id == order.broker_id


@pytest.mark.asyncio
async def test_paper_broker_get_positions_and_account():
    broker = PaperBroker(initial_capital=100_000.0)
    await broker.submit_order("AAPL", "buy", 10)
    positions = await broker.get_positions()
    assert len(positions) == 1
    assert positions[0].symbol == "AAPL"
    assert positions[0].qty == 10
    account = await broker.get_account()
    assert account.cash < 100_000.0
    assert account.equity > 0
    assert account.buying_power > 0


@pytest.mark.asyncio
async def test_paper_broker_cancel_order():
    broker = PaperBroker(initial_capital=100_000.0)
    order = await broker.submit_order("AAPL", "buy", 10, order_type="limit", limit_price=150.0)
    ok = await broker.cancel_order(order.broker_id)
    assert ok is True
    updated = await broker.get_order(order.broker_id)
    assert updated.status == "cancelled"
