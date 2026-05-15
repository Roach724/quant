import pytest
import asyncio
from oms.broker import PaperBroker
from oms.manager import OrderManager
from oms.position import PositionTracker
from execution.twap import TWAPExecutor


def test_signal_to_position_flow():
    broker = PaperBroker(100_000)
    mgr = OrderManager(broker)
    pt = PositionTracker(broker)
    signal = {"symbol": "AAPL", "side": "buy", "qty": 100}
    twap = TWAPExecutor(window_seconds=1, slices=4, randomize=False)
    orders = asyncio.run(twap.run(signal, broker))
    for o in orders:
        pt.record_fill(o.symbol, o.side, o.qty)
    assert pt.positions["AAPL"] == 100


def test_full_lifecycle():
    broker = PaperBroker(100_000)
    mgr = OrderManager(broker)
    t = asyncio.run(mgr.submit("MSFT", "buy", 50))
    assert t.internal_id is not None
    assert t.state == "FILLED"
    assert t.avg_fill_price is not None
