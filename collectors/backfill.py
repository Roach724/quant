"""Historical data backfill script.

Fetches minute-level OHLCV bars for a configurable date range and writes
them to GCS using the existing YFinance adapter and storage module.

Usage:
    # Local run (writes to GCS):
    python collectors/backfill.py \
        --start 2023-01-01 --end 2026-01-01 \
        --symbols SPY,AAPL,MSFT,NVDA \
        --market us

    # Local dry run (writes to local files):
    python collectors/backfill.py \
        --start 2023-01-01 --end 2024-01-01 \
        --local-dir ./backfill_data

    # Cloud Run Job: set env vars and run as-is (no CLI args needed):
    # BACKFILL_START=2023-01-01 BACKFILL_END=2026-01-01
    # BACKFILL_SYMBOLS=SPY,AAPL,MSFT GCS_BUCKET=<bucket>

Env vars (Cloud Run mode):
    GCS_BUCKET: GCS bucket name (required for GCS writes)
    BACKFILL_START: start date (YYYY-MM-DD)
    BACKFILL_END: end date (YYYY-MM-DD)
    BACKFILL_SYMBOLS: comma-separated symbols (default: SPY,AAPL,MSFT,NVDA,GOOGL)
    BACKFILL_CHUNK_DAYS: days per API call (default: 7)
    BACKFILL_SLEEP: seconds between chunks (default: 3)
"""

import argparse
import logging
import os
import sys
import time
from datetime import datetime, timedelta

from adapters.yfinance_adapter import YFinanceUSAdapter
from storage import write_bars_to_gcs, dataframe_to_parquet_bytes, build_gcs_path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def backfill(
    start: str,
    end: str,
    symbols: list[str],
    gcs_bucket: str | None = None,
    local_dir: str | None = None,
    chunk_days: int = 7,
    sleep_seconds: float = 3.0,
):
    """Fetch historical minute bars in chunks and write to GCS or local storage.

    Chunks the date range into windows (default 7 days) to avoid yfinance
    rate limiting and timeout. Writes each chunk as a set of Parquet files
    grouped by symbol-date.
    """
    start_dt = datetime.strptime(start, "%Y-%m-%d")
    end_dt = datetime.strptime(end, "%Y-%m-%d")
    total_days = (end_dt - start_dt).days
    chunks = (total_days + chunk_days - 1) // chunk_days

    logger.info("Backfill: %s → %s (%d days, %d chunks, %d symbols)",
                start, end, total_days, chunks, len(symbols))
    logger.info("Symbols: %s", symbols)
    if gcs_bucket:
        logger.info("Writing to GCS bucket: %s", gcs_bucket)
    elif local_dir:
        logger.info("Writing to local dir: %s", local_dir)
    else:
        logger.error("Either --gcs-bucket or --local-dir is required")
        sys.exit(1)

    adapter = YFinanceUSAdapter()
    total_rows = 0
    chunk_start = start_dt

    for i in range(chunks):
        chunk_end = min(chunk_start + timedelta(days=chunk_days), end_dt)

        logger.info("[%d/%d] Fetching %s → %s ...", i + 1, chunks,
                    chunk_start.strftime("%Y-%m-%d"), chunk_end.strftime("%Y-%m-%d"))

        try:
            df = adapter.fetch_bars(symbols, chunk_start, chunk_end, frequency="1m")
        except Exception as e:
            logger.error("Fetch failed for %s → %s: %s", chunk_start, chunk_end, e)
            chunk_start = chunk_end
            time.sleep(sleep_seconds * 2)  # Extra wait on error
            continue

        if df.empty:
            logger.warning("No data returned for %s → %s", chunk_start, chunk_end)
        else:
            total_rows += len(df)
            if gcs_bucket:
                paths = write_bars_to_gcs(df, gcs_bucket, market="us")
                logger.info("  Wrote %d rows → %d GCS objects", len(df), len(paths))
            elif local_dir:
                _write_local(df, local_dir)

        chunk_start = chunk_end
        if i < chunks - 1:
            time.sleep(sleep_seconds)

    logger.info("Backfill complete. Total rows: %d", total_rows)


def _write_local(df, base_dir: str):
    """Write bars to local filesystem (same Hive-path structure as GCS)."""
    import pandas as pd

    groups = df.groupby(["symbol", df["timestamp"].dt.date])
    for (symbol, _date), group in groups:
        ts = group["timestamp"].iloc[0]
        path = build_gcs_path("us", "bars", symbol, ts)
        full_path = os.path.join(base_dir, path)
        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        group.to_parquet(full_path, index=False)
        # Also write JSON companion for Go API compatibility
        json_path = full_path.replace(".parquet", ".json")
        group.to_json(json_path, orient="records", date_format="iso")
    logger.info("  Wrote %d rows to local dir", len(df))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Backfill historical market data")
    parser.add_argument("--start", default=os.environ.get("BACKFILL_START", ""),
                        help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end", default=os.environ.get("BACKFILL_END", ""),
                        help="End date (YYYY-MM-DD)")
    parser.add_argument("--symbols", default=os.environ.get("BACKFILL_SYMBOLS",
                        "SPY,AAPL,MSFT,NVDA,GOOGL"),
                        help="Comma-separated symbols")
    parser.add_argument("--gcs-bucket", default=os.environ.get("GCS_BUCKET", ""),
                        help="GCS bucket name")
    parser.add_argument("--local-dir", default=os.environ.get("BACKFILL_LOCAL_DIR", ""),
                        help="Local directory for output (instead of GCS)")
    parser.add_argument("--chunk-days", type=int,
                        default=int(os.environ.get("BACKFILL_CHUNK_DAYS", "7")),
                        help="Days per API chunk")
    parser.add_argument("--sleep", type=float,
                        default=float(os.environ.get("BACKFILL_SLEEP", "3")),
                        help="Seconds sleep between chunks")
    args = parser.parse_args()

    if not args.start or not args.end:
        parser.error("--start and --end are required (or set BACKFILL_START/BACKFILL_END)")

    symbols = [s.strip() for s in args.symbols.split(",")]
    backfill(
        start=args.start, end=args.end, symbols=symbols,
        gcs_bucket=args.gcs_bucket or None,
        local_dir=args.local_dir or None,
        chunk_days=args.chunk_days, sleep_seconds=args.sleep,
    )
