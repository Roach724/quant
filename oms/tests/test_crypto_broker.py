"""Tests for crypto broker implementations."""
import pytest
import asyncio
from oms.broker.crypto_broker import CryptoPaperBroker


def test_crypto_paper_submit_market_buy():
    broker = CryptoPaperBroker(initial_capital=10_000.0)
    broker.update_price("BTCUSDT", 65000.0)
    order = asyncio.run(broker.submit_order("BTCUSDT", "buy", 0.01))
    assert order.symbol == "BTCUSDT"
    assert order.side == "buy"
    assert order.qty == 0.01
    assert order.filled_qty == 0.01
    assert order.status == "filled"
    assert order.avg_price is not None


def test_crypto_paper_position_tracking():
    broker = CryptoPaperBroker(initial_capital=10_000.0)
    broker.update_price("ETHUSDT", 3000.0)
    asyncio.run(broker.submit_order("ETHUSDT", "buy", 1.0))
    positions = asyncio.run(broker.get_positions())
    assert len(positions) == 1
    assert positions[0].symbol == "ETHUSDT"
    assert positions[0].qty == 1.0


def test_crypto_paper_account_equity():
    broker = CryptoPaperBroker(initial_capital=10_000.0)
    broker.update_price("BTCUSDT", 50000.0)
    asyncio.run(broker.submit_order("BTCUSDT", "buy", 0.1))
    acc = asyncio.run(broker.get_account())
    assert acc.equity > 0
    assert acc.cash < 10_000.0  # spent money


def test_crypto_paper_cancel_limit_order():
    broker = CryptoPaperBroker(initial_capital=10_000.0)
    broker.update_price("BTCUSDT", 65000.0)
    order = asyncio.run(broker.submit_order(
        "BTCUSDT", "buy", 0.01, order_type="limit", limit_price=60000.0
    ))
    result = asyncio.run(broker.cancel_order(order.broker_id))
    assert result is True


def test_crypto_paper_limit_fills_when_crossed():
    broker = CryptoPaperBroker(initial_capital=10_000.0)
    broker.update_price("BTCUSDT", 65000.0)
    order = asyncio.run(broker.submit_order(
        "BTCUSDT", "buy", 0.01, order_type="limit", limit_price=66000.0
    ))
    assert order.status == "filled"  # current price 65000 <= limit 66000


import os


def test_crypto_binance_broker_requires_credentials():
    from oms.broker.crypto_broker import CryptoBinanceBroker
    if not os.environ.get("BINANCE_API_KEY"):
        with pytest.raises(ValueError):
            CryptoBinanceBroker()
    else:
        broker = CryptoBinanceBroker(testnet=True)
        assert broker is not None


@pytest.mark.vcr
def test_crypto_binance_broker_get_account():
    from oms.broker.crypto_broker import CryptoBinanceBroker
    key = os.environ.get("BINANCE_API_KEY", "test")
    secret = os.environ.get("BINANCE_API_SECRET", "test")
    broker = CryptoBinanceBroker(api_key=key, api_secret=secret, testnet=True)
    acc = asyncio.run(broker.get_account())
    assert acc.cash >= 0
    assert acc.equity >= 0
