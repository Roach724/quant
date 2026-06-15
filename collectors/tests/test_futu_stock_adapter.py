"""Unit tests for FutuStockAdapter — all mocked, no real OpenD connection."""

from datetime import date, datetime, time
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from collectors.adapters.futu_stock_adapter import FutuStockAdapter

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_kline_row(time_key="2026-05-13", o=100.0, h=105.0, low=98.0, c=102.0, v=1000000):
    return pd.Series(
        {
            "time_key": time_key,
            "open": o,
            "high": h,
            "low": low,
            "close": c,
            "volume": v,
        }
    )


def _make_kline_df(rows):
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Tests: _map_frequency
# ---------------------------------------------------------------------------


def test_map_frequency_valid():
    adapter = FutuStockAdapter()
    assert adapter._map_frequency("1m") is not None
    assert adapter._map_frequency("5m") is not None
    assert adapter._map_frequency("15m") is not None
    assert adapter._map_frequency("30m") is not None
    assert adapter._map_frequency("1h") is not None
    assert adapter._map_frequency("1d") is not None
    assert adapter._map_frequency("1w") is not None


def test_map_frequency_invalid():
    adapter = FutuStockAdapter()
    with pytest.raises(ValueError, match="Unsupported frequency"):
        adapter._map_frequency("2m")


# ---------------------------------------------------------------------------
# Tests: _determine_autype
# ---------------------------------------------------------------------------


def test_determine_autype_hk():
    adapter = FutuStockAdapter()
    # HK stocks use QFQ
    assert adapter._determine_autype("HK.00700") != 0  # AuType.QFQ


def test_determine_autype_us():
    adapter = FutuStockAdapter()
    # US stocks use NONE
    from futu import AuType

    assert adapter._determine_autype("US.AAPL") == AuType.NONE


# ---------------------------------------------------------------------------
# Tests: fetch_bars
# ---------------------------------------------------------------------------


def test_fetch_bars_empty_symbols():
    adapter = FutuStockAdapter()
    start = datetime(2026, 5, 11)
    end = datetime(2026, 5, 13)
    df = adapter.fetch_bars([], start, end)
    assert isinstance(df, pd.DataFrame)
    assert len(df) == 0


@patch("collectors.adapters.futu_stock_adapter.OpenQuoteContext")
def test_fetch_bars_single_symbol(mock_ctx_cls):
    mock_ctx = MagicMock()
    mock_ctx_cls.return_value = mock_ctx

    row = _make_kline_row("2026-05-13 10:00:00")
    mock_ctx.request_history_kline.return_value = (0, _make_kline_df([row]), None)

    adapter = FutuStockAdapter()
    start = datetime(2026, 5, 11)
    end = datetime(2026, 5, 13)
    df = adapter.fetch_bars(["HK.00700"], start, end)

    assert isinstance(df, pd.DataFrame)
    assert len(df) == 1
    assert list(df.columns) == ["symbol", "timestamp", "open", "high", "low", "close", "volume", "market"]
    assert df.iloc[0]["symbol"] == "HK.00700"
    assert df.iloc[0]["close"] == 102.0
    assert df.iloc[0]["market"] == "HK"


@patch("collectors.adapters.futu_stock_adapter.OpenQuoteContext")
def test_fetch_bars_pagination(mock_ctx_cls):
    mock_ctx = MagicMock()
    mock_ctx_cls.return_value = mock_ctx

    # First call returns data + a page key
    row1 = _make_kline_row("2026-05-12 10:00:00")
    mock_ctx.request_history_kline.side_effect = [
        (0, _make_kline_df([row1]), "page2"),
        (0, _make_kline_df([_make_kline_row("2026-05-13 10:00:00")]), None),
    ]

    adapter = FutuStockAdapter()
    start = datetime(2026, 5, 11)
    end = datetime(2026, 5, 13)
    df = adapter.fetch_bars(["HK.00700"], start, end)

    assert isinstance(df, pd.DataFrame)
    assert len(df) == 2
    assert mock_ctx.request_history_kline.call_count == 2


@patch("collectors.adapters.futu_stock_adapter.OpenQuoteContext")
def test_fetch_bars_failure_continues(mock_ctx_cls):
    mock_ctx = MagicMock()
    mock_ctx_cls.return_value = mock_ctx

    row = _make_kline_row()
    # First symbol fails, second succeeds
    mock_ctx.request_history_kline.side_effect = [
        (-1, "error message", None),  # failure
        (0, _make_kline_df([row]), None),  # success
    ]

    adapter = FutuStockAdapter()
    start = datetime(2026, 5, 11)
    end = datetime(2026, 5, 13)
    df = adapter.fetch_bars(["HK.00700", "US.AAPL"], start, end)

    # Only the second symbol's data should appear
    assert len(df) == 1
    assert df.iloc[0]["symbol"] == "US.AAPL"
    assert df.iloc[0]["market"] == "US"
    assert mock_ctx.request_history_kline.call_count == 2


# ---------------------------------------------------------------------------
# Tests: fetch_supported_symbols
# ---------------------------------------------------------------------------


def test_fetch_supported_symbols():
    adapter = FutuStockAdapter()
    result = adapter.fetch_supported_symbols()
    assert isinstance(result, list)
    assert result == []


# ---------------------------------------------------------------------------
# Tests: market_hours
# ---------------------------------------------------------------------------


def test_market_hours():
    adapter = FutuStockAdapter()
    open_time, close_time = adapter.market_hours(date(2026, 5, 13))
    assert open_time == time(9, 30)
    assert close_time == time(16, 0)


# ---------------------------------------------------------------------------
# Tests: close
# ---------------------------------------------------------------------------


@patch("collectors.adapters.futu_stock_adapter.OpenQuoteContext")
def test_close(mock_ctx_cls):
    mock_ctx = MagicMock()
    mock_ctx_cls.return_value = mock_ctx

    adapter = FutuStockAdapter()
    # Access _get_ctx to initialise the context
    adapter._get_ctx()
    assert adapter._ctx is not None

    adapter.close()
    mock_ctx.close.assert_called_once()
    assert adapter._ctx is None


def test_close_no_context():
    """close should not raise when no context was ever created."""
    adapter = FutuStockAdapter()
    # Never called _get_ctx, so _ctx is None
    adapter.close()
    assert adapter._ctx is None


def test_close_double_close():
    """close should be safe to call twice."""
    adapter = FutuStockAdapter()
    adapter.close()
    adapter.close()  # no error
