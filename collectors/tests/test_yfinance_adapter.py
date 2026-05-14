from datetime import datetime, timezone
import pandas as pd
import pytest


@pytest.mark.vcr
def test_yfinance_fetch_bars_returns_dataframe():
    from collectors.adapters.yfinance_adapter import YFinanceUSAdapter

    adapter = YFinanceUSAdapter()
    start = datetime(2026, 5, 11, tzinfo=timezone.utc)
    end = datetime(2026, 5, 13, tzinfo=timezone.utc)

    df = adapter.fetch_bars(["AAPL"], start, end, frequency="1m")

    assert isinstance(df, pd.DataFrame)
    assert len(df) > 0
    assert "symbol" in df.columns
    assert "close" in df.columns
    assert "market" in df.columns
    assert all(df["market"] == "US")
    assert df["timestamp"].dtype.kind == "M"


def test_yfinance_symbols_returns_list():
    from collectors.adapters.yfinance_adapter import YFinanceUSAdapter

    adapter = YFinanceUSAdapter()
    symbols = adapter.fetch_supported_symbols()

    assert isinstance(symbols, list)
    assert "AAPL" in symbols


def test_yfinance_market_hours_returns_tuple():
    from datetime import date
    from collectors.adapters.yfinance_adapter import YFinanceUSAdapter

    adapter = YFinanceUSAdapter()
    open_time, close_time = adapter.market_hours(date(2026, 5, 13))

    assert open_time.hour == 9
    assert open_time.minute == 30
    assert close_time.hour == 16
