"""Historical data backfill script.

Fetches minute-level OHLCV bars for a configurable date range and writes
them to GCS using available market adapters (YFinance US/HK, Crypto Binance, Alpaca US).

Usage:
    # US stocks (default):
    python collectors/backfill.py \
        --start 2023-01-01 --end 2026-01-01 \
        --symbols SPY,AAPL,MSFT,NVDA \
        --market us

    # Crypto via Binance (daily bars recommended for multi-month backfill):
    python collectors/backfill.py \
        --start 2025-01-01 --end 2026-01-01 \
        --source cryptobinance --all \
        --frequency 1d --gcs-bucket <bucket>

    # Hong Kong stocks:
    python collectors/backfill.py \
        --start 2025-01-01 --end 2026-01-01 \
        --source yfinancehk --all \
        --frequency 1d --gcs-bucket <bucket>

    # Local dry run:
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
    BACKFILL_SOURCE: data source — "yfinance" (US), "alpaca" (US),
                     "cryptobinance" (crypto), "yfinancehk" (HK)
"""

import argparse
import logging
import os
import sys
import time
from datetime import datetime, timedelta, timezone

from adapters.yfinance_adapter import YFinanceUSAdapter
from adapters.crypto_binance_adapter import CryptoBinanceAdapter
from adapters.yfinance_hk_adapter import YFinanceHKAdapter
from storage import build_gcs_path, dataframe_to_parquet_bytes, write_bars_to_gcs

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# Chunk size recommendations by frequency:
#   "1m", "5m" → 7 days (yfinance free limit: last 30 days only)
#   "15m", "30m" → 30 days
#   "1h" → 60 days
#   "1d" → 365 days (daily data has no time restriction)
FREQUENCY_DEFAULTS = {"chunk_days": {"1m": 7, "5m": 7, "15m": 30, "30m": 30, "1h": 60, "1d": 365}}  # fmt: skip


def backfill(
    start: str,
    end: str,
    symbols: list[str],
    gcs_bucket: str | None = None,
    local_dir: str | None = None,
    chunk_days: int | None = None,
    sleep_seconds: float = 3.0,
    frequency: str = "1m",
    source: str = "yfinance",
):
    """Fetch historical bars in chunks and write to GCS or local storage.

    Args:
        frequency: Bar interval — "1m", "5m", "15m", "30m", "1h", "1d".
                   Minute data (1m, 5m) only available for last 30 days
                   from yfinance free tier. Use "1d" for multi-year backfill.
        source: Data adapter — "yfinance" (US, free), "alpaca" (US, needs auth),
                "cryptobinance" (Binance crypto), "yfinancehk" (HK stocks).
    """
    start_dt = datetime.strptime(start, "%Y-%m-%d")
    end_dt = datetime.strptime(end, "%Y-%m-%d")
    total_days = (end_dt - start_dt).days

    if chunk_days is None:
        chunk_days = FREQUENCY_DEFAULTS["chunk_days"].get(frequency, 7)
    chunks = max(1, (total_days + chunk_days - 1) // chunk_days)

    logger.info("Backfill: %s → %s (%d days, %d chunks, %d symbols, freq=%s, source=%s)",
                start, end, total_days, chunks, len(symbols), frequency, source)
    logger.info("Symbols: %s", symbols)
    if gcs_bucket:
        logger.info("Writing to GCS bucket: %s", gcs_bucket)
    elif local_dir:
        logger.info("Writing to local dir: %s", local_dir)
    else:
        logger.error("Either --gcs-bucket or --local-dir is required")
        sys.exit(1)

    if source == "alpaca":
        from adapters.alpaca_adapter import AlpacaUSAdapter
        key = os.environ.get("ALPACA_API_KEY", "")
        secret = os.environ.get("ALPACA_API_SECRET", "")
        if not key or not secret:
            logger.error("Alpaca source requires ALPACA_API_KEY and ALPACA_API_SECRET env vars")
            sys.exit(1)
        adapter = AlpacaUSAdapter(api_key=key, api_secret=secret)
    elif source == "cryptobinance":
        adapter = CryptoBinanceAdapter()
    elif source == "yfinancehk":
        adapter = YFinanceHKAdapter()
    else:
        adapter = YFinanceUSAdapter()

    market = adapter.market.lower()

    total_rows = 0
    chunk_start = start_dt

    for i in range(chunks):
        chunk_end = min(chunk_start + timedelta(days=chunk_days), end_dt)

        logger.info("[%d/%d] Fetching %s → %s (freq=%s)...", i + 1, chunks,
                    chunk_start.strftime("%Y-%m-%d"), chunk_end.strftime("%Y-%m-%d"), frequency)

        try:
            df = adapter.fetch_bars(symbols, chunk_start, chunk_end, frequency=frequency)
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
                paths = write_bars_to_gcs(df, gcs_bucket, market=market, frequency=frequency)
                logger.info("  Wrote %d rows → %d GCS objects", len(df), len(paths))
            elif local_dir:
                _write_local(df, local_dir, market, frequency)

        chunk_start = chunk_end
        if i < chunks - 1:
            time.sleep(sleep_seconds)

    logger.info("Backfill complete. Total rows: %d", total_rows)


def _write_local(df, base_dir: str, market: str, frequency: str = "5m"):
    """Write bars to local filesystem (same Hive-path structure as GCS)."""
    import pandas as pd

    df = df.copy()
    df["_ingest_time"] = datetime.now(timezone.utc)

    groups = df.groupby(["symbol", df["timestamp"].dt.date])
    for (symbol, _date), group in groups:
        ts = group["timestamp"].iloc[0]
        path = build_gcs_path(market, "bars", frequency, symbol, ts)
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
    parser.add_argument("--symbols", default=os.environ.get("BACKFILL_SYMBOLS", ""),
                        help="Comma-separated symbols (default: all S&P 500)")
    parser.add_argument("--all", action="store_true",
                        help="Fetch all S&P 500 symbols (ignored if --symbols specified)")
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
    parser.add_argument("--frequency", default=os.environ.get("BACKFILL_FREQUENCY", "1m"),
                        choices=["1m", "5m", "15m", "30m", "1h", "1d"],
                        help="Bar frequency (default: 1m. Use 1d for multi-year backfill)")
    parser.add_argument("--source", default=os.environ.get("BACKFILL_SOURCE", "yfinance"),
                        choices=["yfinance", "alpaca", "cryptobinance", "yfinancehk"],
                        help="Data source adapter (default: yfinance)")
    args = parser.parse_args()

    if not args.start or not args.end:
        parser.error("--start and --end are required (or set BACKFILL_START/BACKFILL_END)")

    if args.symbols:
        symbols = [s.strip() for s in args.symbols.split(",")]
    elif args.all or os.environ.get("BACKFILL_ALL"):
        # Use the adapter that matches the selected source for symbol discovery
        if args.source == "cryptobinance":
            symbols = CryptoBinanceAdapter().fetch_supported_symbols()
        elif args.source == "yfinancehk":
            symbols = YFinanceHKAdapter().fetch_supported_symbols()
        elif args.source == "alpaca":
            from adapters.alpaca_adapter import AlpacaUSAdapter
            key = os.environ.get("ALPACA_API_KEY", "")
            secret = os.environ.get("ALPACA_API_SECRET", "")
            symbols = AlpacaUSAdapter(api_key=key, api_secret=secret).fetch_supported_symbols()
        else:
            symbols = YFinanceUSAdapter().fetch_supported_symbols()
        logger.info("Using all %d symbols from %s", len(symbols), args.source)
    else:
        symbols = ["SPY", "AAPL", "MSFT", "NVDA", "GOOGL"]  # minimal default
    backfill(
        start=args.start, end=args.end, symbols=symbols,
        gcs_bucket=args.gcs_bucket or None,
        local_dir=args.local_dir or None,
        chunk_days=args.chunk_days or None,
        sleep_seconds=args.sleep,
        frequency=args.frequency,
        source=args.source,
    )
