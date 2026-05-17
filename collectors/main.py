"""Cloud Run Job entrypoint for data collection.

Env vars:
    GCS_BUCKET: GCS bucket name (required)
    COLLECTOR_SOURCE: "yfinance" (default) or "alpaca"
    ALPACA_API_KEY, ALPACA_API_SECRET: required if source=alpaca
    SYMBOLS: comma-separated symbols (default: SPY,AAPL,MSFT). Set to "AUTO"
             or omit for yfinance 1d to auto-discover full stock pool (US + HK).
    FREQUENCY: bar frequency (default: 1m)
    LOOKBACK_MINUTES: minutes to look back (default: 60)
"""

import logging
import os
import sys
from datetime import datetime, timezone, timedelta

from adapters.alpaca_adapter import AlpacaUSAdapter
from adapters.yfinance_adapter import YFinanceUSAdapter
from adapters.yfinance_hk_adapter import YFinanceHKAdapter
from adapters.akshare_hk_adapter import AkshareHKAdapter
from adapters.akshare_us_adapter import AkshareUSAdapter
from adapters.crypto_binance_adapter import CryptoBinanceAdapter
from storage import write_bars_to_gcs

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


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
    if source == "yfinance" and frequency == "1d":
        # US 1d: use yfinance with akshare fallback
        return YFinanceUSAdapter(fallback_adapter=AkshareUSAdapter())
    return YFinanceUSAdapter()


def get_symbols(source: str, frequency: str) -> list[str]:
    """Determine symbol list from env var or auto-discovery.

    When SYMBOLS is "AUTO" (or not set) for HK 1d, the full HK stock pool is
    fetched dynamically. For 5m, symbols from env var are used as-is.
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

    # Fallback defaults
    if source == "cryptobinance":
        return CryptoBinanceAdapter().fetch_supported_symbols()

    return ["SPY", "AAPL", "MSFT"]


def main():
    bucket = os.environ["GCS_BUCKET"]
    source = os.environ.get("COLLECTOR_SOURCE", "yfinance")
    frequency = os.environ.get("FREQUENCY", "1m")
    lookback = int(os.environ.get("LOOKBACK_MINUTES", "60"))

    symbols = get_symbols(source, frequency)
    end = datetime.now(timezone.utc)
    start = end - timedelta(minutes=lookback)

    logger.info("Starting collection: source=%s symbols=%d range=%s..%s freq=%s",
                source, len(symbols), start.isoformat(), end.isoformat(), frequency)

    try:
        adapter = get_adapter(source, frequency)
        df = adapter.fetch_bars(symbols, start, end, frequency=frequency)

        if df.empty:
            logger.warning("No data returned for %d symbols", len(symbols))
            return

        paths = write_bars_to_gcs(df, bucket, market=adapter.market.lower(), frequency=frequency)
        logger.info("Wrote %d rows to %d GCS paths", len(df), len(paths))
    except Exception:
        logger.exception("Collection failed")
        sys.exit(1)


if __name__ == "__main__":
    main()
