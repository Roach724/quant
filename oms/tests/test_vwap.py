import pytest
import asyncio
import numpy as np
from execution.vwap import VWAPExecutor
from oms.broker import PaperBroker


def test_vwap_equal_without_profile():
    broker = PaperBroker(100_000)
    vwap = VWAPExecutor(window_seconds=1, slices=4)
    orders = asyncio.run(vwap.run({"symbol": "AAPL", "side": "buy", "qty": 100}, broker))
    assert len(orders) == 4
    assert all(o.status == "filled" for o in orders)


def test_vwap_with_profile():
    broker = PaperBroker(100_000)
    profile = np.array([0.1, 0.2, 0.3, 0.4])
    vwap = VWAPExecutor(window_seconds=1, slices=4, volume_profile=profile)
    orders = asyncio.run(vwap.run({"symbol": "AAPL", "side": "buy", "qty": 100}, broker))
    assert orders[3].qty > orders[0].qty
