"""Tests for YFinanceHKAdapter."""
import pytest
from datetime import datetime, timezone, date
from collectors.adapters.yfinance_hk_adapter import YFinanceHKAdapter


def test_adapter_has_hk_market():
    adapter = YFinanceHKAdapter()
    assert adapter.market == "HK"

def test_adapter_symbols_include_tencent():
    adapter = YFinanceHKAdapter()
    symbols = adapter.fetch_supported_symbols()
    assert isinstance(symbols, list)
    assert "0700" in symbols

def test_adapter_market_hours_hkt():
    adapter = YFinanceHKAdapter()
    open_time, close_time = adapter.market_hours(date.today())
    assert open_time.hour == 9 and open_time.minute == 30
    assert close_time.hour == 16 and close_time.minute == 0

@pytest.mark.vcr
def test_fetch_bars_daily_returns_dataframe():
    adapter = YFinanceHKAdapter()
    start = datetime(2026, 5, 1, tzinfo=timezone.utc)
    end = datetime(2026, 5, 16, tzinfo=timezone.utc)
    df = adapter.fetch_bars(["0700.HK", "9988.HK"], start, end, frequency="1d")
    assert len(df) > 0
    assert "symbol" in df.columns
    assert "close" in df.columns
    assert "market" in df.columns
    assert all(df["market"] == "HK")
    # Symbols should have .HK stripped
    assert all("0700" in df["symbol"].values or "9988" in df["symbol"].values)
