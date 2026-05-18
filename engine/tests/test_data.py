import pandas as pd
import numpy as np
from engine.data import DataFrameSource


def test_dataframe_source():
    dates = pd.date_range("2026-01-01", periods=10, freq="D")
    close = pd.DataFrame({"AAPL": np.random.randn(10) + 100, "MSFT": np.random.randn(10) + 300}, index=dates)
    src = DataFrameSource(close=close)
    assert len(src) == 10
    assert src.universe == ["AAPL", "MSFT"]
    assert src.close.shape == (10, 2)
    assert src.timestamp[0] == dates[0]


def test_dataframe_source_iloc():
    dates = pd.date_range("2026-01-01", periods=3, freq="D")
    close = pd.DataFrame({"AAPL": [100.0, 101.0, 102.0]}, index=dates)
    src = DataFrameSource(close=close)
    row = src.iloc(1)
    assert row["close"]["AAPL"] == 101.0


def test_dataframe_source_with_pred():
    """Verify pred data flows through iloc."""
    dates = pd.date_range("2026-01-01", periods=3, freq="D")
    close = pd.DataFrame({"AAPL": [100.0, 101.0, 102.0]}, index=dates)
    pred = pd.DataFrame({"AAPL": [0.1, 0.5, 0.9]}, index=dates)
    src = DataFrameSource(close=close, pred=pred)
    row = src.iloc(1)
    assert row["close"]["AAPL"] == 101.0
    assert row["pred"]["AAPL"] == 0.5


def test_dataframe_source_without_pred():
    """Verify backward compatibility: no pred still works."""
    dates = pd.date_range("2026-01-01", periods=3, freq="D")
    close = pd.DataFrame({"AAPL": [100.0, 101.0, 102.0]}, index=dates)
    src = DataFrameSource(close=close)  # no pred arg
    row = src.iloc(1)
    assert "pred" not in row
    assert row["close"]["AAPL"] == 101.0


def test_dataframe_source_returns_ohlcv():
    """Verify iloc returns open/high/low/volume when available."""
    dates = pd.date_range("2026-01-01", periods=3, freq="D")
    close = pd.DataFrame({"AAPL": [100.0, 101.0, 102.0]}, index=dates)
    open_ = pd.DataFrame({"AAPL": [99.0, 100.0, 101.0]}, index=dates)
    high = pd.DataFrame({"AAPL": [101.0, 102.0, 103.0]}, index=dates)
    low = pd.DataFrame({"AAPL": [98.0, 99.0, 100.0]}, index=dates)
    volume = pd.DataFrame({"AAPL": [1000, 2000, 3000]}, index=dates)
    src = DataFrameSource(close=close, open=open_, high=high, low=low, volume=volume)
    row = src.iloc(1)
    assert row["open"]["AAPL"] == 100.0
    assert row["high"]["AAPL"] == 102.0
    assert row["low"]["AAPL"] == 99.0
    assert row["volume"]["AAPL"] == 2000
