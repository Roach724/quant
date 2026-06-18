"""Cloud Run Job entrypoint for data collection.

Env vars:
    GCS_BUCKET: GCS bucket name (required)
    COLLECTOR_SOURCE: "yfinance" (default) or "alpaca"
    ALPACA_API_KEY, ALPACA_API_SECRET: *** if source=alpaca
    SYMBOLS: comma-separated symbols (default: SPY,AAPL,MSFT). Set to "AUTO"
             or omit for yfinance 1d to auto-discover full stock pool (US + HK).
    FREQUENCY: bar frequency (default: 1m)
    LOOKBACK_MINUTES: minutes to look back (default: 60)
    MARKET: market filter for futu_stock source (e.g. "us", "hk")
"""

import logging
import os
import signal
import sys
import threading
from datetime import UTC, datetime, timedelta

from adapters.akshare_hk_adapter import AkshareHKAdapter
from adapters.akshare_us_adapter import AkshareUSAdapter
from adapters.alpaca_adapter import AlpacaUSAdapter
from adapters.crypto_binance_adapter import CryptoBinanceAdapter
from adapters.crypto_futu_adapter import CryptoFutuAdapter
from adapters.futu_stock_adapter import FutuStockAdapter
from adapters.yfinance_adapter import YFinanceUSAdapter
from adapters.yfinance_hk_adapter import YFinanceHKAdapter

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# Sentinel flag for signal-driven graceful shutdown.
_shutdown_requested = False


def _signal_handler(signum, frame):
    """Set shutdown flag on SIGTERM/SIGINT so the main loop can exit early."""
    global _shutdown_requested
    sig_name = signal.Signals(signum).name
    logger.warning("Received %s — initiating graceful shutdown", sig_name)
    _shutdown_requested = True


def get_adapter(source: str, frequency: str = "1m"):
    if source == "alpaca":
        api_key = os.environ["ALPACA_API_KEY"]
        api_secret = os.environ["ALPACA_API_SECRET"]
        return AlpacaUSAdapter(api_key=api_key, api_secret=api_secret)
    if source == "cryptobinance":
        return CryptoBinanceAdapter()
    if source == "yfinancehk":
        # HK 1d: use yfinance with akshare fallback
        # HK 5m: yfinance only (no fallback for minute data)
        if frequency == "1d":
            return YFinanceHKAdapter(fallback_adapter=AkshareHKAdapter())
        return YFinanceHKAdapter()
    if source == "futu_stock":
        return FutuStockAdapter()
    if source == "futu_crypto":
        return CryptoFutuAdapter()
    if source == "yfinance" and frequency == "1d":
        # US 1d: use yfinance with akshare fallback
        return YFinanceUSAdapter(fallback_adapter=AkshareUSAdapter())
    return YFinanceUSAdapter()


def get_symbols(source: str, frequency: str, market: str | None = None) -> list[str]:
    """Determine symbol list from env var or auto-discovery.

    When SYMBOLS is "AUTO" (or not set) for HK 1d, the full HK stock pool is
    fetched dynamically. For 5m, symbols from env var are used as-is.

    If `market` is set (e.g. "us", "hk"), symbols are filtered by prefix.
    """
    symbols_str = os.environ.get("SYMBOLS", "").strip()

    # Explicit symbol list takes priority
    if symbols_str and symbols_str.upper() != "AUTO":
        return [s.strip() for s in symbols_str.split(",")]

    # Auto-discovery: for HK daily
    if source == "yfinancehk" and frequency == "1d":
        symbols = YFinanceHKAdapter.fetch_all_symbols()
        # fetch_all_symbols returns 4-digit codes; adapter.fetch_bars expects .HK suffix
        symbols = [f"{s}.HK" for s in symbols]
        logger.info("Auto-discovered %d HK symbols", len(symbols))
        return symbols

    # Auto-discovery: for US daily
    if source == "yfinance" and frequency == "1d":
        symbols = YFinanceUSAdapter.fetch_all_symbols()
        logger.info("Auto-discovered %d US symbols", len(symbols))
        return symbols

    # Auto-discovery: Futu sources (with optional market filter)
    if source == "futu_stock":
        futu_adapter = FutuStockAdapter()
        try:
            symbols = futu_adapter.fetch_supported_symbols()
            total_count = len(symbols)
            if market:
                prefix = f"{market.upper()}."
                symbols = [s for s in symbols if s.startswith(prefix)]
                logger.info("Futu stock: %d total → %d after market=%s filter", total_count, len(symbols), market)
            else:
                logger.info("Auto-discovered %d futu stock symbols (no market filter)", len(symbols))
        finally:
            futu_adapter.close()
        return symbols
    if source == "futu_crypto":
        futu_adapter = CryptoFutuAdapter()
        try:
            symbols = futu_adapter.fetch_supported_symbols()
        finally:
            futu_adapter.close()
        logger.info("Auto-discovered %d futu crypto symbols", len(symbols))
        return symbols

    # Fallback defaults
    if source == "cryptobinance":
        return CryptoBinanceAdapter().fetch_supported_symbols()

    return ["SPY", "AAPL", "MSFT"]


def main():
    _bucket = os.environ["GCS_BUCKET"]
    source = os.environ.get("COLLECTOR_SOURCE", "yfinance")
    frequency = os.environ.get("FREQUENCY", "1m")
    lookback = int(os.environ.get("LOOKBACK_MINUTES", "60"))
    market = os.environ.get("MARKET", "").strip() or None

    # ------------------------------------------------------------------
    # Global timeout: LOOKBACK_MINUTES × 2 + 300 s (5 min slack).
    # If the process hasn't exited by then, force a hard exit to prevent
    # zombie processes that hang around for hours.
    # ------------------------------------------------------------------
    timeout_seconds = (lookback * 2) + 300
    _start_time = datetime.now(UTC)

    def _deadline_timer():
        elapsed = (datetime.now(UTC) - _start_time).total_seconds()
        if elapsed >= timeout_seconds:
            logger.error(
                "Global timeout reached (%d s elapsed, limit %d s) — forcing exit",
                elapsed,
                timeout_seconds,
            )
            os._exit(1)

    deadline_timer = threading.Timer(timeout_seconds, _deadline_timer)
    deadline_timer.daemon = True
    deadline_timer.start()

    # ------------------------------------------------------------------
    # Install signal handlers so we shut down cleanly when Cloud Run /
    # Kubernetes sends SIGTERM.
    # ------------------------------------------------------------------
    signal.signal(signal.SIGTERM, _signal_handler)
    signal.signal(signal.SIGINT, _signal_handler)

    symbols = get_symbols(source, frequency, market)
    end = datetime.now(UTC)
    start = end - timedelta(minutes=lookback)

    logger.info(
        "Starting collection: source=%s market=%s symbols=%d range=%s..%s freq=%s",
        source,
        market or "all",
        len(symbols),
        start.isoformat(),
        end.isoformat(),
        frequency,
    )

    adapter = None
    try:
        adapter = get_adapter(source, frequency)

        # Check for early shutdown before expensive fetch.
        if _shutdown_requested:
            logger.warning("Shutdown requested before fetch — exiting")
            return

        df = adapter.fetch_bars(symbols, start, end, frequency=frequency)

        if df.empty:
            logger.warning("No data returned for %d symbols", len(symbols))
            return

        from common.bq_writer import write_bars_to_bq

        market_dir = os.environ.get("MARKET", adapter.market.lower())
        table = f"{market_dir}_bars_{frequency}"
        n = write_bars_to_bq(df, table_id=table)
        logger.info("Wrote %d rows to BQ table %s", n, table)
    except Exception:
        logger.exception("Collection failed")
        sys.exit(1)
    finally:
        # Close adapters that hold persistent connections (e.g. OpenQuoteContext
        if adapter is not None:
            try:
                adapter.close()
            except Exception:
                pass

        # Cancel the deadline timer if it hasn't fired yet.
        try:
            deadline_timer.cancel()
        except Exception:
            pass


if __name__ == "__main__":
    main()
