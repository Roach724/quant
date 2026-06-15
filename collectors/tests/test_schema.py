# collectors/tests/test_schema.py
from datetime import UTC, datetime

from collectors.schema import Bar


def test_bar_creation():
    bar = Bar(
        symbol="AAPL",
        timestamp=datetime(2026, 5, 13, 10, 0, tzinfo=UTC),
        open=189.50,
        high=190.20,
        low=189.30,
        close=189.80,
        volume=1000000,
        market="US",
        frequency="1m",
    )
    assert bar.symbol == "AAPL"
    assert bar.close == 189.80
    assert bar.market == "US"


def test_bar_to_parquet_roundtrip(tmp_path):
    import pandas as pd

    bars = [
        Bar(
            symbol="AAPL",
            timestamp=datetime(2026, 5, 13, 10, i, tzinfo=UTC),
            open=100.0 + i,
            high=101.0 + i,
            low=99.0 + i,
            close=100.5 + i,
            volume=1000 * i,
            market="US",
            frequency="1m",
        )
        for i in range(5)
    ]
    df = pd.DataFrame([b.__dict__ for b in bars])
    path = tmp_path / "test.parquet"
    df.to_parquet(path, index=False)

    loaded = pd.read_parquet(path)
    assert len(loaded) == 5
    assert list(loaded.columns) == [
        "symbol", "timestamp", "open", "high", "low", "close", "volume", "market", "frequency"
    ]
