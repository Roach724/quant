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
from adapters.akshare_hk_adapter import AkshareHKAdapter
from adapters.akshare_us_adapter import AkshareUSAdapter
from adapters.futu_stock_adapter import FutuStockAdapter
from adapters.crypto_futu_adapter import CryptoFutuAdapter
from storage import build_gcs_path, dataframe_to_parquet_bytes
from common.bq_writer import write_bars_to_bq

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# Chunk size recommendations by frequency:
#   "1m", "5m" → 7 days (yfinance free limit: last 30 days only)
#   "15m", "30m" → 30 days
#   "1h" → 60 days
#   "1d" → 365 days (daily data has no time restriction)
FREQUENCY_DEFAULTS = {"chunk_days": {"1m": 7, "5m": 7, "15m": 30, "30m": 30, "1h": 60, "1d": 365}}  # fmt: skip

# HK-specific: delay between symbols to avoid rate-limiting
HK_SYMBOL_DELAY = 0.3  # seconds
HK_MAX_RETRIES = 2
HK_PROGRESS_INTERVAL = 50  # log every N symbols

# US-specific: delay between symbols to avoid rate-limiting
US_SYMBOL_DELAY = 0.3  # seconds
US_MAX_RETRIES = 2
US_PROGRESS_INTERVAL = 50  # log every N symbols


def _replace_existing_bars(market: str, frequency: str, start: str, end: str, table_id: str = None):
    """Delete existing bars in date range for idempotent backfill."""
    from google.cloud import bigquery as _bq
    client = _bq.Client(project="deductive-notch-495015-c2")
    table_name = table_id or f"{market}_bars_{frequency}"
    table_ref = f"deductive-notch-495015-c2.quant.{table_name}"
    logger.info("Replace mode: deleting existing data from %s (%s → %s)", table_ref, start, end)
    query = f"""
    DELETE FROM `{table_ref}`
    WHERE DATE(timestamp) BETWEEN '{start}' AND '{end}'
    """
    try:
        job = client.query(query)
        job.result()
        logger.info("  DELETE completed: %d rows removed", job.num_dml_affected_rows)
    except Exception as e:
        logger.warning("  DELETE failed (table may not exist yet): %s", e)


def _backfill_us(
    adapter: YFinanceUSAdapter,
    symbols: list[str],
    start_dt: datetime,
    end_dt: datetime,
    frequency: str,
    gcs_bucket: str | None,
    local_dir: str | None,
    chunk_days: int = 365,
):
    """Backfill US stocks: per-symbol serial processing with yfinance→akshare fallback.

    Each symbol is fetched independently in time-based chunks. Failed symbols
    are retried up to US_MAX_RETRIES times with exponential backoff.
    """
    market = adapter.market.lower()
    total_rows = 0
    failed_symbols = []

    def _prefix_symbols(df):
        if market == "us" and frequency == "5m":
            df = df.copy()
            mask = ~df["symbol"].astype(str).str.startswith("US.")
            df.loc[mask, "symbol"] = "US." + df.loc[mask, "symbol"].astype(str)
        return df

    for idx, sym in enumerate(symbols):
        sym_rows = 0

        for attempt in range(1 + US_MAX_RETRIES):
            try:
                chunk_start = start_dt
                while chunk_start < end_dt:
                    chunk_end = min(chunk_start + timedelta(days=chunk_days), end_dt)
                    df = adapter.fetch_bars([sym], chunk_start, chunk_end, frequency=frequency)
                    if df is not None and not df.empty:
                        sym_rows += len(df)
                        if local_dir:
                            _write_local(df, local_dir, market, frequency)
                        write_bars_to_bq(_prefix_symbols(df), table_id=table_id or f"{market}_bars_{frequency}")
                    chunk_start = chunk_end
                    if chunk_start < end_dt:
                        time.sleep(0.1)  # small pause between chunks for same symbol
                break  # success
            except Exception as e:
                if attempt < US_MAX_RETRIES:
                    wait = US_SYMBOL_DELAY * (2 ** (attempt + 1))
                    logger.warning("Symbol %s attempt %d failed: %s. Retrying in %.1fs...",
                                   sym, attempt + 1, e, wait)
                    time.sleep(wait)
                else:
                    logger.error("Symbol %s failed after %d retries: %s", sym, US_MAX_RETRIES, e)
                    failed_symbols.append(sym)

        if sym_rows > 0:
            total_rows += sym_rows

        if (idx + 1) % US_PROGRESS_INTERVAL == 0:
            logger.info("Progress: %d/%d symbols processed, %d rows collected so far",
                        idx + 1, len(symbols), total_rows)

        # Rate-limit between symbols
        if idx < len(symbols) - 1:
            time.sleep(US_SYMBOL_DELAY)

    logger.info("US backfill complete: %d/%d symbols succeeded, %d total rows",
                len(symbols) - len(failed_symbols), len(symbols), total_rows)
    if failed_symbols:
        logger.warning("Failed symbols (%d): %s", len(failed_symbols), failed_symbols[:20])


def _backfill_hk(
    adapter: YFinanceHKAdapter,
    symbols: list[str],
    start_dt: datetime,
    end_dt: datetime,
    frequency: str,
    gcs_bucket: str | None,
    local_dir: str | None,
    chunk_days: int = 365,
    table_id: str = None,
):
    """Backfill HK stocks: per-symbol serial processing with yfinance→akshare fallback.

    Each symbol is fetched independently in time-based chunks. Failed symbols
    are retried up to HK_MAX_RETRIES times with exponential backoff.
    """
    market = adapter.market.lower()
    total_rows = 0
    failed_symbols = []

    def _prefix_symbols(df):
        df = df.copy()
        if market == "us" and frequency == "5m":
            mask = ~df["symbol"].astype(str).str.startswith("US.")
            df.loc[mask, "symbol"] = "US." + df.loc[mask, "symbol"].astype(str)
        elif market == "hk":
            mask = ~df["symbol"].astype(str).str.startswith("HK.")
            df.loc[mask, "symbol"] = "HK." + df.loc[mask, "symbol"].astype(str).str.zfill(5)
        return df

    for idx, sym in enumerate(symbols):
        # Convert SSOT format (HK.00001) to yfinance format (0001.HK)
        from common.normalize import normalize_symbol
        bare = normalize_symbol(sym, market)
        yf_sym = f"{bare}.HK"
        sym_rows = 0

        for attempt in range(1 + HK_MAX_RETRIES):
            try:
                chunk_start = start_dt
                while chunk_start < end_dt:
                    chunk_end = min(chunk_start + timedelta(days=chunk_days), end_dt)
                    df = adapter.fetch_bars([yf_sym], chunk_start, chunk_end, frequency=frequency)
                    if df is not None and not df.empty:
                        sym_rows += len(df)
                        if local_dir:
                            _write_local(df, local_dir, market, frequency)
                        write_bars_to_bq(_prefix_symbols(df), table_id=table_id or f"{market}_bars_{frequency}")
                    chunk_start = chunk_end
                    if chunk_start < end_dt:
                        time.sleep(0.1)  # small pause between chunks for same symbol
                break  # success
            except Exception as e:
                if attempt < HK_MAX_RETRIES:
                    wait = HK_SYMBOL_DELAY * (2 ** (attempt + 1))
                    logger.warning("Symbol %s attempt %d failed: %s. Retrying in %.1fs...",
                                   sym, attempt + 1, e, wait)
                    time.sleep(wait)
                else:
                    logger.error("Symbol %s failed after %d retries: %s", sym, HK_MAX_RETRIES, e)
                    failed_symbols.append(sym)

        if sym_rows > 0:
            total_rows += sym_rows

        if (idx + 1) % HK_PROGRESS_INTERVAL == 0:
            logger.info("Progress: %d/%d symbols processed, %d rows collected so far",
                        idx + 1, len(symbols), total_rows)

        # Rate-limit between symbols
        if idx < len(symbols) - 1:
            time.sleep(HK_SYMBOL_DELAY)

    logger.info("HK backfill complete: %d/%d symbols succeeded, %d total rows",
                len(symbols) - len(failed_symbols), len(symbols), total_rows)
    if failed_symbols:
        logger.warning("Failed symbols (%d): %s", len(failed_symbols), failed_symbols[:20])


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
    market: str = None,
    replace: bool = False,
    skip_existing: bool = True,
    table_id: str = None,
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

    logger.info("Backfill: %s → %s (%d days, %d symbols, freq=%s, source=%s)",
                start, end, total_days, len(symbols), frequency, source)
    logger.info("Symbols: %s", symbols[:20] if len(symbols) > 20 else symbols)
    logger.info("Writing directly to BigQuery")

    # --- Replace mode: delete existing data in date range before writing ---
    if replace:
        storage_market = market if market else ("us" if source in ("yfinance", "alpaca", "futu_stock") else "hk" if source == "yfinancehk" else "crypto")
        _replace_existing_bars(storage_market, frequency, start, end, table_id=table_id)
    elif not skip_existing:
        logger.info("Append mode (skip_existing=False): all rows will be inserted")

    # --- HK: per-symbol serial processing with fallback ---
    if source == "yfinancehk":
        hk_adapter = YFinanceHKAdapter(fallback_adapter=AkshareHKAdapter())
        if chunk_days is None:
            chunk_days = FREQUENCY_DEFAULTS["chunk_days"].get(frequency, 365)
        _backfill_hk(
            adapter=hk_adapter,
            symbols=symbols,
            start_dt=start_dt,
            end_dt=end_dt,
            frequency=frequency,
            gcs_bucket=gcs_bucket,
            local_dir=local_dir,
            chunk_days=chunk_days,
            table_id=table_id,
        )
        return

    # --- US: per-symbol serial processing with fallback ---
    if source == "yfinance":
        us_adapter = YFinanceUSAdapter(fallback_adapter=AkshareUSAdapter())
        if chunk_days is None:
            chunk_days = FREQUENCY_DEFAULTS["chunk_days"].get(frequency, 365)
        _backfill_us(
            adapter=us_adapter,
            symbols=symbols,
            start_dt=start_dt,
            end_dt=end_dt,
            frequency=frequency,
            gcs_bucket=gcs_bucket,
            local_dir=local_dir,
            chunk_days=chunk_days,
            table_id=table_id,
        )
        return

    # --- Standard bulk-fetch path (Alpaca, Crypto, Futu) ---
    if chunk_days is None:
        chunk_days = FREQUENCY_DEFAULTS["chunk_days"].get(frequency, 7)
    chunks = max(1, (total_days + chunk_days - 1) // chunk_days)

    if source == "futu_stock":
        adapter = FutuStockAdapter()
    elif source == "futu_crypto":
        adapter = CryptoFutuAdapter()
    elif source == "alpaca":
        from adapters.alpaca_adapter import AlpacaUSAdapter
        key = os.environ.get("ALPACA_API_KEY", "")
        secret = os.environ.get("ALPACA_API_SECRET", "")
        if not key or not secret:
            logger.error("Alpaca source requires ALPACA_API_KEY and ALPACA_API_SECRET env vars")
            sys.exit(1)
        adapter = AlpacaUSAdapter(api_key=key, api_secret=secret)
    elif source == "cryptobinance":
        adapter = CryptoBinanceAdapter()
    else:
        adapter = YFinanceUSAdapter()

    # Use --market arg for storage path, fallback to adapter.market
    storage_market = market if market else adapter.market.lower()

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
            write_bars_to_bq(df, table_id=table_id or f"{storage_market}_bars_{frequency}")
            logger.info("  Wrote %d rows -> BQ table %s", len(df), f"{storage_market}_bars_{frequency}")

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
                        choices=["yfinance", "alpaca", "cryptobinance", "yfinancehk",
                                 "futu_stock", "futu_crypto"],
                        help="Data source adapter (default: yfinance)")
    parser.add_argument("--market", default="", choices=["us", "hk", ""], help="Filter symbols by market prefix (e.g. --market us for US. only)")
    parser.add_argument("--replace", action="store_true",
                        help="Delete existing data in date range before backfill (DANGER: may lose data if API incomplete)")
    parser.add_argument("--skip-existing", action="store_true", default=True,
                        help="Skip rows already in BQ (default, safe)")
    parser.add_argument("--table", default="",
                        help="Override BQ table name (default: {market}_bars_{freq})")
    args = parser.parse_args()

    if not args.start or not args.end:
        parser.error("--start and --end are required (or set BACKFILL_START/BACKFILL_END)")

    if args.symbols:
        symbols = [s.strip() for s in args.symbols.split(",")]
    elif args.source == "yfinance":
        # US 1d: auto-discover full stock pool
        symbols = YFinanceUSAdapter.fetch_all_symbols()
        logger.info("Auto-discovered %d US symbols from %s", len(symbols), args.source)
    elif args.all or os.environ.get("BACKFILL_ALL"):
        # Use the adapter that matches the selected source for symbol discovery
        if args.source == "cryptobinance":
            symbols = CryptoBinanceAdapter().fetch_supported_symbols()
        elif args.source == "futu_stock":
            symbols = FutuStockAdapter().fetch_supported_symbols()
        elif args.source == "futu_crypto":
            symbols = CryptoFutuAdapter().fetch_supported_symbols()
        elif args.source == "yfinancehk":
            symbols = YFinanceHKAdapter.fetch_all_symbols()
        elif args.source == "alpaca":
            from adapters.alpaca_adapter import AlpacaUSAdapter
            key = os.environ.get("ALPACA_API_KEY", "")
            secret = os.environ.get("ALPACA_API_SECRET", "")
            symbols = AlpacaUSAdapter(api_key=key, api_secret=secret).fetch_supported_symbols()
        else:
            symbols = YFinanceUSAdapter.fetch_all_symbols()
        logger.info("Using all %d symbols from %s", len(symbols), args.source)
    else:
        symbols = ["SPY", "AAPL", "MSFT", "NVDA", "GOOGL"]  # minimal default

    # Market filter — apply after symbol resolution
    if args.market:
        prefix = f"{args.market.upper()}."
        symbols = [s for s in symbols if s.startswith(prefix)]
        if not symbols:
            logger.error("No symbols match market filter '%s'", args.market)
            sys.exit(1)
        logger.info("Filtered to %d symbols for market=%s", len(symbols), args.market.upper())
    backfill(
        start=args.start, end=args.end, symbols=symbols,
        gcs_bucket=args.gcs_bucket or None,
        local_dir=args.local_dir or None,
        chunk_days=args.chunk_days or None,
        sleep_seconds=args.sleep,
        frequency=args.frequency,
        source=args.source,
        market=args.market or None,
        replace=args.replace,
        skip_existing=args.skip_existing,
        table_id=args.table or None,
    )
