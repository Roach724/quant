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
