"""Cloud Run Job entrypoint for data collection.

Env vars:
    GCS_BUCKET: GCS bucket name (required)
    COLLECTOR_SOURCE: "yfinance" (default) or "alpaca"
    ALPACA_API_KEY, ALPACA_API_SECRET: required if source=alpaca
    SYMBOLS: comma-separated symbols (default: SPY,AAPL,MSFT)
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
from adapters.crypto_binance_adapter import CryptoBinanceAdapter
from storage import write_bars_to_gcs

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def get_adapter(source: str):
    if source == "alpaca":
        api_key = os.environ["ALPACA_API_KEY"]
        api_secret = os.environ["ALPACA_API_SECRET"]
        return AlpacaUSAdapter(api_key=api_key, api_secret=api_secret)
    if source == "cryptobinance":
        return CryptoBinanceAdapter()
    if source == "yfinancehk":
        return YFinanceHKAdapter()
    return YFinanceUSAdapter()


def main():
    bucket = os.environ["GCS_BUCKET"]
    source = os.environ.get("COLLECTOR_SOURCE", "yfinance")
    symbols_str = os.environ.get("SYMBOLS", "SPY,AAPL,MSFT")
    frequency = os.environ.get("FREQUENCY", "1m")
    lookback = int(os.environ.get("LOOKBACK_MINUTES", "60"))

    symbols = [s.strip() for s in symbols_str.split(",")]
    end = datetime.now(timezone.utc)
    start = end - timedelta(minutes=lookback)

    logger.info("Starting collection: source=%s symbols=%s range=%s..%s",
                source, symbols, start.isoformat(), end.isoformat())

    try:
        adapter = get_adapter(source)
        df = adapter.fetch_bars(symbols, start, end, frequency=frequency)

        if df.empty:
            logger.warning("No data returned for symbols=%s", symbols)
            return

        paths = write_bars_to_gcs(df, bucket, market=adapter.market.lower(), frequency=frequency)
        logger.info("Wrote %d rows to %d GCS paths", len(df), len(paths))
    except Exception:
        logger.exception("Collection failed")
        sys.exit(1)


if __name__ == "__main__":
    main()
