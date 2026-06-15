"""Unit tests for CryptoFutuAdapter — all mocked, no real OpenD connection."""

from datetime import date, datetime, time
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from collectors.adapters.crypto_futu_adapter import CryptoFutuAdapter

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_kline_row(time_key="2026-05-13 10:00:00", o=100.0, h=105.0, low=98.0, c=102.0, v=1000000):
    return pd.Series({
        "time_key": time_key,
        "open": o,
        "high": h,
        "low": low,
        "close": c,
        "volume": v,
    })


def _make_kline_df(rows):
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# 1. test_to_futu_code
# ---------------------------------------------------------------------------

def test_to_futu_code():
    adapter = CryptoFutuAdapter()
    assert adapter._to_futu_code("BTC/USDT") == "CC.BTCUSDT"
    assert adapter._to_futu_code("ETH/USDT") == "CC.ETHUSDT"
    assert adapter._to_futu_code("SOL/USDT") == "CC.SOLUSDT"


# ---------------------------------------------------------------------------
# 2 & 3. _map_frequency
# ---------------------------------------------------------------------------

def test_map_frequency_valid():
    adapter = CryptoFutuAdapter()
    assert adapter._map_frequency("1m") is not None
    assert adapter._map_frequency("5m") is not None
    assert adapter._map_frequency("15m") is not None
    assert adapter._map_frequency("30m") is not None
    assert adapter._map_frequency("1h") is not None
    assert adapter._map_frequency("1d") is not None
    assert adapter._map_frequency("1w") is not None


def test_map_frequency_invalid():
    adapter = CryptoFutuAdapter()
    with pytest.raises(ValueError, match="Unsupported frequency"):
        adapter._map_frequency("2m")


# ---------------------------------------------------------------------------
# 4. fetch_bars empty symbols
# ---------------------------------------------------------------------------

def test_fetch_bars_empty_symbols():
    adapter = CryptoFutuAdapter()
    start = datetime(2026, 5, 11)
    end = datetime(2026, 5, 13)
    df = adapter.fetch_bars([], start, end)
    assert isinstance(df, pd.DataFrame)
    assert len(df) == 0


# ---------------------------------------------------------------------------
# 5. fetch_bars single symbol
# ---------------------------------------------------------------------------

@patch("collectors.adapters.crypto_futu_adapter.OpenQuoteContext")
def test_fetch_bars_single_symbol(mock_ctx_cls):
    mock_ctx = MagicMock()
    mock_ctx_cls.return_value = mock_ctx

    row = _make_kline_row("2026-05-13 10:00:00")
    mock_ctx.request_history_kline.return_value = (0, _make_kline_df([row]), None)

    adapter = CryptoFutuAdapter()
    start = datetime(2026, 5, 11)
    end = datetime(2026, 5, 13)
    df = adapter.fetch_bars(["BTC/USDT"], start, end)

    assert isinstance(df, pd.DataFrame)
    assert len(df) == 1
    assert list(df.columns) == ["symbol", "timestamp", "open", "high", "low", "close", "volume", "market"]
    assert df.iloc[0]["symbol"] == "BTC/USDT"
    assert df.iloc[0]["close"] == 102.0
    assert df.iloc[0]["market"] == "CRYPTO"

    # Verify the adapter converted "BTC/USDT" → "CC.BTCUSDT" before calling API
    mock_ctx.request_history_kline.assert_called_once()
    call_args = mock_ctx.request_history_kline.call_args
    assert call_args[0][0] == "CC.BTCUSDT"


# ---------------------------------------------------------------------------
# 6. fetch_bars pagination
# ---------------------------------------------------------------------------

@patch("collectors.adapters.crypto_futu_adapter.OpenQuoteContext")
def test_fetch_bars_pagination(mock_ctx_cls):
    mock_ctx = MagicMock()
    mock_ctx_cls.return_value = mock_ctx

    row1 = _make_kline_row("2026-05-12 10:00:00")
    mock_ctx.request_history_kline.side_effect = [
        (0, _make_kline_df([row1]), "page2"),
        (0, _make_kline_df([_make_kline_row("2026-05-13 10:00:00")]), None),
    ]

    adapter = CryptoFutuAdapter()
    start = datetime(2026, 5, 11)
    end = datetime(2026, 5, 13)
    df = adapter.fetch_bars(["ETH/USDT"], start, end)

    assert isinstance(df, pd.DataFrame)
    assert len(df) == 2
    assert mock_ctx.request_history_kline.call_count == 2

    # Both calls should use the converted Futu code
    for call in mock_ctx.request_history_kline.call_args_list:
        assert call[0][0] == "CC.ETHUSDT"


# ---------------------------------------------------------------------------
# 7. fetch_bars failure continues
# ---------------------------------------------------------------------------

@patch("collectors.adapters.crypto_futu_adapter.OpenQuoteContext")
def test_fetch_bars_failure_continues(mock_ctx_cls):
    mock_ctx = MagicMock()
    mock_ctx_cls.return_value = mock_ctx

    row = _make_kline_row("2026-05-13 10:00:00")
    mock_ctx.request_history_kline.side_effect = [
        (-1, "error message", None),
        (0, _make_kline_df([row]), None),
    ]

    adapter = CryptoFutuAdapter()
    start = datetime(2026, 5, 11)
    end = datetime(2026, 5, 13)
    df = adapter.fetch_bars(["BTC/USDT", "ETH/USDT"], start, end)

    assert len(df) == 1
    assert df.iloc[0]["symbol"] == "ETH/USDT"
    assert mock_ctx.request_history_kline.call_count == 2

    # First call: BTC/USDT → CC.BTCUSDT
    assert mock_ctx.request_history_kline.call_args_list[0][0][0] == "CC.BTCUSDT"
    # Second call: ETH/USDT → CC.ETHUSDT
    assert mock_ctx.request_history_kline.call_args_list[1][0][0] == "CC.ETHUSDT"


# ---------------------------------------------------------------------------
# 8. fetch_supported_symbols
# ---------------------------------------------------------------------------

def test_fetch_supported_symbols():
    adapter = CryptoFutuAdapter()
    result = adapter.fetch_supported_symbols()
    assert isinstance(result, list)
    assert len(result) == 10
    assert "BTC/USDT" in result
    assert "ETH/USDT" in result
    assert "UNI/USDT" in result


# ---------------------------------------------------------------------------
# 9. market_hours
# ---------------------------------------------------------------------------

def test_market_hours():
    adapter = CryptoFutuAdapter()
    open_time, close_time = adapter.market_hours(date(2026, 5, 13))
    assert open_time == time(0, 0)
    assert close_time == time(23, 59, 59)


# ---------------------------------------------------------------------------
# 10. close
# ---------------------------------------------------------------------------

@patch("collectors.adapters.crypto_futu_adapter.OpenQuoteContext")
def test_close(mock_ctx_cls):
    mock_ctx = MagicMock()
    mock_ctx_cls.return_value = mock_ctx

    adapter = CryptoFutuAdapter()
    adapter._get_ctx()
    assert adapter._ctx is not None

    adapter.close()
    mock_ctx.close.assert_called_once()
    assert adapter._ctx is None


def test_close_no_context():
    """close should not raise when no context was ever created."""
    adapter = CryptoFutuAdapter()
    adapter.close()
    assert adapter._ctx is None


def test_close_double_close():
    """close should be safe to call twice."""
    adapter = CryptoFutuAdapter()
    adapter.close()
    adapter.close()  # no error
