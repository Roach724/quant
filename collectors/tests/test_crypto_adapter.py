"""Tests for CryptoBinanceAdapter."""
import pytest
from datetime import datetime, timezone
from collectors.adapters.crypto_binance_adapter import CryptoBinanceAdapter


def test_adapter_has_crypto_market():
    adapter = CryptoBinanceAdapter()
    assert adapter.market == "CRYPTO"

def test_adapter_symbols_include_btc():
    adapter = CryptoBinanceAdapter()
    symbols = adapter.fetch_supported_symbols()
    assert isinstance(symbols, list)
    assert "BTCUSDT" in symbols

def test_adapter_market_hours_24x7():
    from datetime import date
    adapter = CryptoBinanceAdapter()
    open_time, close_time = adapter.market_hours(date.today())
    assert open_time.hour == 0 and open_time.minute == 0
    assert close_time.hour == 23 and close_time.minute == 59

@pytest.mark.vcr
def test_fetch_bars_returns_valid_dataframe():
    adapter = CryptoBinanceAdapter()
    start = datetime(2026, 5, 15, tzinfo=timezone.utc)
    end = datetime(2026, 5, 16, tzinfo=timezone.utc)
    df = adapter.fetch_bars(["BTC/USDT"], start, end, frequency="1h")
    assert len(df) > 0
    assert "symbol" in df.columns
    assert "close" in df.columns
    assert "market" in df.columns
    assert all(df["market"] == "CRYPTO")
