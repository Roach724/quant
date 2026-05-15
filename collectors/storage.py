import io
from datetime import datetime

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq


def build_gcs_path(market: str, data_type: str, symbol: str, timestamp: datetime) -> str:
    """Build GCS object path with Hive-style partitioning for BigQuery compatibility.

    Format: raw/{market}/{data_type}/year={YYYY}/month={MM}/day={DD}/symbol={SYMBOL}.parquet
    """
    return (
        f"raw/{market.lower()}/{data_type}/"
        f"year={timestamp.year:04d}/month={timestamp.month:02d}/day={timestamp.day:02d}/"
        f"symbol={symbol}.parquet"
    )


def dataframe_to_parquet_bytes(df: pd.DataFrame) -> bytes:
    """Convert a DataFrame to Parquet bytes with Snappy compression."""
    table = pa.Table.from_pandas(df, preserve_index=False)
    buf = io.BytesIO()
    pq.write_table(table, buf, compression="snappy")
    return buf.getvalue()


def write_bars_to_gcs(
    df: pd.DataFrame,
    bucket_name: str,
    market: str = "us",
) -> list[str]:
    """Write bars DataFrame to GCS, one file per symbol-date combination. Returns list of GCS paths."""
    from google.cloud import storage

    client = storage.Client()
    bucket = client.bucket(bucket_name)
    paths = []

    groups = df.groupby(["symbol", df["timestamp"].dt.date])
    for (symbol, _date), group in groups:
        ts = group["timestamp"].iloc[0]
        path = build_gcs_path(market, "bars", symbol, ts)
        blob = bucket.blob(path)
        blob.upload_from_string(
            dataframe_to_parquet_bytes(group),
            content_type="application/octet-stream",
        )
        paths.append(f"gs://{bucket_name}/{path}")

    return paths
