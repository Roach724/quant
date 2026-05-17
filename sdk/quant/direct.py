import logging
import os

import pandas as pd

logger = logging.getLogger(__name__)


def bars_direct(
    symbols: str | list[str],
    start: str,
    end: str,
    market: str = "us",
    frequency: str = "5m",
    base_path: str | None = None,
    cache_dir: str | None = None,
) -> pd.DataFrame:
    if isinstance(symbols, str):
        symbols = [symbols]

    start_dt = pd.Timestamp(start)
    end_dt = pd.Timestamp(end)

    cache = cache_dir or os.environ.get("QUANT_CACHE_DIR", "")
    bucket = base_path or os.environ.get("QUANT_GCS_BUCKET", "")

    if cache:
        os.makedirs(cache, exist_ok=True)

    is_gcs = bucket and not bucket.startswith("/") and not bucket.startswith(".") and "\\" not in bucket and ":" not in bucket
    if bucket.startswith("gs://"):
        is_gcs = True
        bucket = bucket[5:]

    if is_gcs and cache:
        return _read_with_cache(symbols, start_dt, end_dt, market, frequency, bucket, cache)
    elif is_gcs:
        return _read_from_gcs(symbols, start_dt, end_dt, market, frequency, bucket)
    else:
        return _read_from_local(symbols, start_dt, end_dt, market, frequency, bucket)


def _cache_path(cache_dir: str, market: str, frequency: str, symbol: str, d) -> str:
    return (
        f"{cache_dir}/raw/{market}/bars/"
        f"freq={frequency}/"
        f"year={d.year:04d}/month={d.month:02d}/"
        f"day={d.day:02d}/symbol={symbol}.parquet"
    )


def _read_with_cache(symbols, start_dt, end_dt, market, frequency, bucket, cache_dir):
    """Read from local cache; on miss, fetch from GCS and cache locally."""
    frames = []
    date_range = pd.date_range(start_dt, end_dt, freq="D")

    for symbol in symbols:
        for d in date_range:
            local_path = _cache_path(cache_dir, market, frequency, symbol, d)

            # Check cache first
            if os.path.exists(local_path):
                try:
                    df = pd.read_parquet(local_path)
                    frames.append(df)
                    continue
                except Exception:
                    pass

            # Cache miss — try GCS
            gcs_path = (
                f"{bucket}/raw/{market}/bars/"
                f"freq={frequency}/"
                f"year={d.year:04d}/month={d.month:02d}/day={d.day:02d}/"
                f"symbol={symbol}.parquet"
            )
            try:
                import gcsfs
                fs = gcsfs.GCSFileSystem()
                with fs.open(gcs_path, "rb") as f:
                    df = pd.read_parquet(f)
                frames.append(df)
                # Cache locally for next time
                os.makedirs(os.path.dirname(local_path), exist_ok=True)
                df.to_parquet(local_path, index=False)
            except Exception as e:
                logger.debug("GCS miss for %s: %s", gcs_path, e)
                continue

    if not frames:
        return pd.DataFrame()

    result = pd.concat(frames, ignore_index=True)
    result["timestamp"] = pd.to_datetime(result["timestamp"], utc=True)
    return result.set_index(["symbol", "timestamp"]).sort_index()


def _read_from_local(symbols, start_dt, end_dt, market, frequency, base_path):
    frames = []
    for symbol in symbols:
        date_range = pd.date_range(start_dt, end_dt, freq="D")
        for d in date_range:
            path = (
                f"{base_path or '.'}/raw/{market}/bars/"
                f"freq={frequency}/"
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


def _read_from_gcs(symbols, start_dt, end_dt, market, frequency, bucket):
    import gcsfs

    fs = gcsfs.GCSFileSystem()
    frames = []

    for symbol in symbols:
        date_range = pd.date_range(start_dt, end_dt, freq="D")
        for d in date_range:
            path = (
                f"{bucket}/raw/{market}/bars/"
                f"freq={frequency}/"
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
