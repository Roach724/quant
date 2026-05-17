import os
from datetime import datetime, timezone
import pandas as pd
import pytest
from collectors.schema import Bar
from collectors.storage import build_gcs_path, dataframe_to_parquet_bytes


def test_build_gcs_path():
    ts = datetime(2026, 5, 13, 10, 30, tzinfo=timezone.utc)
    path = build_gcs_path(market="us", data_type="bars", frequency="5m", symbol="AAPL", timestamp=ts)
    assert path == "raw/us/bars/freq=5m/year=2026/month=05/day=13/symbol=AAPL.parquet"


def test_build_gcs_path_cn_market():
    ts = datetime(2026, 5, 13, 10, 30, tzinfo=timezone.utc)
    path = build_gcs_path(market="cn", data_type="bars", frequency="1d", symbol="000001", timestamp=ts)
    assert path == "raw/cn/bars/freq=1d/year=2026/month=05/day=13/symbol=000001.parquet"


def test_dataframe_to_parquet_bytes():
    bar = Bar(
        symbol="AAPL",
        timestamp=datetime(2026, 5, 13, 10, 0, tzinfo=timezone.utc),
        open=100.0, high=101.0, low=99.0, close=100.5,
        volume=1000, market="US", frequency="1m",
    )
    df = pd.DataFrame([bar.__dict__])
    buf = dataframe_to_parquet_bytes(df)
    assert isinstance(buf, bytes)
    assert len(buf) > 0

    import io
    loaded = pd.read_parquet(io.BytesIO(buf))
    assert loaded.iloc[0]["symbol"] == "AAPL"
