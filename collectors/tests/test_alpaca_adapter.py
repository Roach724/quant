from datetime import datetime, timezone
import os
from unittest.mock import patch

import pandas as pd
import pytest


@pytest.mark.vcr
def test_alpaca_fetch_bars_returns_dataframe():
    from collectors.adapters.alpaca_adapter import AlpacaUSAdapter

    api_key = os.environ.get("ALPACA_API_KEY", "test-key")
    api_secret = os.environ.get("ALPACA_API_SECRET", "test-secret")
    adapter = AlpacaUSAdapter(api_key=api_key, api_secret=api_secret)

    start = datetime(2026, 5, 11, tzinfo=timezone.utc)
    end = datetime(2026, 5, 13, tzinfo=timezone.utc)

    df = adapter.fetch_bars(["AAPL", "MSFT"], start, end, frequency="1m")

    assert isinstance(df, pd.DataFrame)
    assert len(df) > 0
    assert set(df["symbol"].unique()) == {"AAPL", "MSFT"}
    assert all(df["market"] == "US")


def test_alpaca_symbols():
    from collectors.adapters.alpaca_adapter import AlpacaUSAdapter

    adapter = AlpacaUSAdapter(api_key="test", api_secret="test")

    # Mock the API call to avoid requiring real credentials
    class MockLatestBarResponse:
        data = {"AAPL": [], "MSFT": [], "TSLA": []}

    with patch.object(
        adapter._client,
        "get_stock_latest_bar",
        return_value=MockLatestBarResponse(),
    ):
        symbols = adapter.fetch_supported_symbols()

    assert isinstance(symbols, list)
    assert len(symbols) > 0


def test_alpaca_adapter_implements_protocol():
    from collectors.adapters.alpaca_adapter import AlpacaUSAdapter
    from collectors.adapters.base import MarketAdapter

    adapter = AlpacaUSAdapter(api_key="test", api_secret="test")
    assert isinstance(adapter, MarketAdapter)
    assert adapter.market == "US"
