"""Test that Broker protocol supports float quantities."""
import pytest
import asyncio
from oms.broker import PaperBroker, BrokerOrder, BrokerPosition, BrokerAccount


def test_broker_order_accepts_float_qty():
    order = BrokerOrder(
        broker_id="test-1", symbol="BTCUSDT", side="buy", qty=0.001
    )
    assert order.qty == 0.001
    assert isinstance(order.qty, float)


def test_broker_position_accepts_float_qty():
    pos = BrokerPosition(
        symbol="BTCUSDT", qty=0.5, avg_entry_price=65000.0,
        market_value=32500.0, unrealized_pnl=100.0
    )
    assert pos.qty == 0.5


def test_paper_broker_submit_float_qty():
    broker = PaperBroker(initial_capital=100_000)
    broker.update_price("BTCUSDT", 65000.0)
    order = asyncio.run(broker.submit_order("BTCUSDT", "buy", 0.001))
    assert order.qty == 0.001
    assert order.filled_qty == 0.001
    assert order.status == "filled"


def test_paper_broker_float_position():
    broker = PaperBroker(initial_capital=100_000)
    broker.update_price("BTCUSDT", 65000.0)
    asyncio.run(broker.submit_order("BTCUSDT", "buy", 0.5))
    positions = asyncio.run(broker.get_positions())
    assert len(positions) == 1
    assert positions[0].qty == 0.5
