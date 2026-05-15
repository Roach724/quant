from datetime import datetime, timezone
import pandas as pd
from quant.direct import bars_direct


def test_bars_direct_reads_parquet_from_gcs(tmp_path):
    import pyarrow.parquet as pq
    import pyarrow as pa

    data_dir = tmp_path / "raw/us/bars/year=2026/month=05/day=13"
    data_dir.mkdir(parents=True)
    table = pa.table({
        "symbol": ["AAPL"] * 3,
        "timestamp": [
            datetime(2026, 5, 13, 10, 0, tzinfo=timezone.utc),
            datetime(2026, 5, 13, 10, 1, tzinfo=timezone.utc),
            datetime(2026, 5, 13, 10, 2, tzinfo=timezone.utc),
        ],
        "open": [100.0, 101.0, 102.0],
        "high": [101.0, 102.0, 103.0],
        "low": [99.0, 100.0, 101.0],
        "close": [100.5, 101.5, 102.5],
        "volume": [1000, 1100, 1200],
        "market": ["US"] * 3,
        "frequency": ["1m"] * 3,
    })
    pq.write_table(table, data_dir / "symbol=AAPL.parquet")

    df = bars_direct(
        "AAPL", "2026-05-13", "2026-05-13",
        market="us", base_path=str(tmp_path),
    )

    assert isinstance(df, pd.DataFrame)
    assert len(df) == 3
    assert list(df.index.get_level_values("symbol")) == ["AAPL"] * 3
