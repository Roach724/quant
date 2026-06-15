# collectors/tests/test_adapter_protocol.py
from collectors.adapters.base import MarketAdapter


class FakeAdapter:
    market = "US"

    def fetch_bars(self, symbols, start, end, frequency="1m"):
        pass

    def fetch_supported_symbols(self):
        pass

    def market_hours(self, d):
        pass


def test_adapter_is_protocol():
    adapter = FakeAdapter()
    assert isinstance(adapter, MarketAdapter)


def test_adapter_protocol_requires_market():
    from typing import get_type_hints

    get_type_hints(MarketAdapter)
    assert "market" in MarketAdapter.__annotations__
