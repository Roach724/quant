import io
from datetime import datetime

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq


def build_gcs_path(market: str, data_type: str, symbol: str, timestamp: datetime) -> str:
    return (
        f"raw/{market.lower()}/{data_type}/"
        f"{timestamp.year:04d}/{timestamp.month:02d}/{timestamp.day:02d}/"
        f"{symbol}.parquet"
    )


def dataframe_to_parquet_bytes(df: pd.DataFrame) -> bytes:
    table = pa.Table.from_pandas(df, preserve_index=False)
    buf = io.BytesIO()
    pq.write_table(table, buf, compression="snappy")
    return buf.getvalue()


def write_bars_to_gcs(
    df: pd.DataFrame,
    bucket_name: str,
    market: str = "us",
) -> list[str]:
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
