import os
import pandas as pd


def bars_direct(
    symbols: str | list[str],
    start: str,
    end: str,
    market: str = "us",
    base_path: str | None = None,
) -> pd.DataFrame:
    if isinstance(symbols, str):
        symbols = [symbols]

    start_dt = pd.Timestamp(start)
    end_dt = pd.Timestamp(end)

    bucket = base_path or os.environ.get("QUANT_GCS_BUCKET", "")
    if bucket.startswith("gs://"):
        return _read_from_gcs(symbols, start_dt, end_dt, market, bucket)
    elif bucket and not bucket.startswith("/") and not bucket.startswith(".") and "\\" not in bucket and ":" not in bucket:
        return _read_from_gcs(symbols, start_dt, end_dt, market, bucket)
    return _read_from_local(symbols, start_dt, end_dt, market, bucket)


def _read_from_local(symbols, start_dt, end_dt, market, base_path):
    frames = []
    for symbol in symbols:
        date_range = pd.date_range(start_dt, end_dt, freq="D")
        for d in date_range:
            path = (
                f"{base_path or ''}/raw/{market}/bars/"
                f"year={d.year:04d}/month={d.month:02d}/day={d.day:02d}/"
                f"symbol={symbol}.parquet"
            )
            try:
                df = pd.read_parquet(path)
                frames.append(df)
            except FileNotFoundError:
                continue

    if not frames:
        return pd.DataFrame()

    result = pd.concat(frames, ignore_index=True)
    result["timestamp"] = pd.to_datetime(result["timestamp"], utc=True)
    return result.set_index(["symbol", "timestamp"]).sort_index()


def _read_from_gcs(symbols, start_dt, end_dt, market, bucket):
    import gcsfs

    if bucket.startswith("gs://"):
        bucket = bucket[5:]

    fs = gcsfs.GCSFileSystem()
    frames = []

    for symbol in symbols:
        date_range = pd.date_range(start_dt, end_dt, freq="D")
        for d in date_range:
            path = (
                f"{bucket}/raw/{market}/bars/"
                f"year={d.year:04d}/month={d.month:02d}/day={d.day:02d}/"
                f"symbol={symbol}.parquet"
            )
            try:
                with fs.open(path, "rb") as f:
                    df = pd.read_parquet(f)
                frames.append(df)
            except FileNotFoundError:
                continue

    if not frames:
        return pd.DataFrame()

    result = pd.concat(frames, ignore_index=True)
    result["timestamp"] = pd.to_datetime(result["timestamp"], utc=True)
    return result.set_index(["symbol", "timestamp"]).sort_index()
