import io
from datetime import datetime, timezone

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq


def build_gcs_path(market: str, data_type: str, frequency: str, symbol: str, timestamp: datetime) -> str:
    """Build GCS object path with Hive-style partitioning for BigQuery compatibility.

    Format: raw/{market}/{data_type}/freq={freq}/year={YYYY}/month={MM}/day={DD}/symbol={SYMBOL}.parquet
    """
    return (
        f"raw/{market.lower()}/{data_type}/"
        f"freq={frequency}/"
        f"year={timestamp.year:04d}/month={timestamp.month:02d}/day={timestamp.day:02d}/"
        f"symbol={symbol}.parquet"
    )


def dataframe_to_parquet_bytes(df: pd.DataFrame) -> bytes:
    """Convert a DataFrame to Parquet bytes with Snappy compression.
    
    Timestamp columns are cast to microsecond precision for BigQuery
    compatibility (BQ rejects nanosecond TIMESTAMP_NANOS).
    """
    table = pa.Table.from_pandas(df, preserve_index=False)
    # Cast timestamp columns to microseconds for BigQuery compatibility
    for i, field in enumerate(table.schema):
        if pa.types.is_timestamp(field.type):
            col = table.column(i).cast(pa.timestamp("us", tz=field.type.tz))
            table = table.set_column(i, field.with_type(pa.timestamp("us", tz=field.type.tz)), col)
    buf = io.BytesIO()
    pq.write_table(table, buf, compression="snappy")
    return buf.getvalue()


def write_bars_to_gcs(
    df: pd.DataFrame,
    bucket_name: str,
    market: str = "us",
    frequency: str = "5m",
) -> list[str]:
    """Write bars DataFrame to GCS atomically.

    Uses a temp-file-then-rename pattern to avoid readers (BigQuery loader)
    seeing partially-written or corrupted Parquet files:
        1. Upload to {symbol}.parquet.tmp
        2. Server-side copy tmp → final path (overwrites atomically)
        3. Delete tmp

    Returns list of final GCS paths.
    """
    from google.cloud import storage

    df = df.copy()
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    df["_ingest_time"] = datetime.now(timezone.utc)

    client = storage.Client()
    bucket = client.bucket(bucket_name)
    paths = []

    groups = df.groupby(["symbol", df["timestamp"].dt.date])
    for (symbol, _date), group in groups:
        ts = group["timestamp"].iloc[0]
        final_path = build_gcs_path(market, "bars", frequency, symbol, ts)
        tmp_path = final_path + ".tmp"

        parquet_bytes = dataframe_to_parquet_bytes(group)

        # 1. Upload to temp path
        tmp_blob = bucket.blob(tmp_path)
        tmp_blob.upload_from_string(
            parquet_bytes,
            content_type="application/octet-stream",
        )

        # 2. Server-side copy to final path (atomic overwrite)
        final_blob = bucket.blob(final_path)
        final_blob.rewrite(tmp_blob)

        # 3. Delete temp
        tmp_blob.delete()

        paths.append(f"gs://{bucket_name}/{final_path}")

    return paths
