import pytest
from oms.broker import PaperBroker
from oms.manager import OrderManager


@pytest.mark.asyncio
async def test_order_manager_submit_market():
    broker = PaperBroker(initial_capital=100_000.0)
    manager = OrderManager(broker)
    t = await manager.submit("AAPL", "buy", 10, strategy_name="test_strat", signal_id="sig123")
    assert t.state == "FILLED"
    assert t.broker_id is not None
    assert t.symbol == "AAPL"
    assert t.strategy_name == "test_strat"
    assert t.signal_id == "sig123"
    assert len(manager.orders) == 1


@pytest.mark.asyncio
async def test_order_manager_submit_limit_and_cancel():
    broker = PaperBroker(initial_capital=100_000.0)
    manager = OrderManager(broker)
    t = await manager.submit("AAPL", "sell", 5, order_type="limit", limit_price=155.0)
    assert t.state == "PENDING"
    ok = await manager.cancel(t.internal_id)
    assert ok is True
    assert manager.orders[t.internal_id].state == "CANCELLED"


@pytest.mark.asyncio
async def test_order_manager_get_open_orders():
    broker = PaperBroker(initial_capital=100_000.0)
    manager = OrderManager(broker)
    t1 = await manager.submit("AAPL", "buy", 10, order_type="limit", limit_price=149.0)
    t2 = await manager.submit("MSFT", "buy", 20)
    open_orders = manager.get_open_orders()
    assert len(open_orders) == 1
    assert open_orders[0].internal_id == t1.internal_id
