import pytest
import asyncio
from execution.twap import TWAPExecutor
from oms.broker import PaperBroker


def test_twap_executes():
    broker = PaperBroker(100_000)
    twap = TWAPExecutor(window_seconds=1, slices=3, randomize=False)
    orders = asyncio.run(twap.run({"symbol": "AAPL", "side": "buy", "qty": 90}, broker))
    assert len(orders) == 3
    assert all(o.status == "filled" for o in orders)


def test_twap_randomize():
    broker = PaperBroker(100_000)
    twap = TWAPExecutor(window_seconds=1, slices=2, randomize=True)
    orders = asyncio.run(twap.run({"symbol": "MSFT", "side": "sell", "qty": 50}, broker))
    assert len(orders) == 2
