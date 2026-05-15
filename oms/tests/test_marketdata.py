import pytest
import asyncio
from oms.broker.market_data import MarketDataStream


def test_connect():
    md = MarketDataStream()
    asyncio.run(md.connect(["AAPL"]))
    assert md._connected is True


def test_on_bar_callback():
    md = MarketDataStream()
    received = []
    md.on_bar(lambda bar: received.append(bar))
    assert len(md._bar_callbacks) == 1


def test_latest_bar():
    md = MarketDataStream()
    bar = asyncio.run(md.latest_bar("AAPL"))
    assert bar["symbol"] == "AAPL"
