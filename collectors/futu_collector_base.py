"""Base class for Futu API data collectors with rate limiting, retry, and GCS write."""

import logging
import time
from datetime import UTC, datetime

import pandas as pd
from futu import RET_OK, OpenQuoteContext

logger = logging.getLogger(__name__)

OPEND_HOST = "127.0.0.1"
OPEND_PORT = 11111
GCS_BUCKET = "deductive-notch-495015-c2-quant-data"


class FutuCollector:
    """Base collector for Futu API data sources."""

    def __init__(self, market: str, rate_limit_per_min: int = 30):
        self.market = market
        self.rate_limit_per_min = rate_limit_per_min
        self._ctx: OpenQuoteContext | None = None
        self._request_count = 0
        self._window_start = time.time()

    def _get_ctx(self) -> OpenQuoteContext:
        if self._ctx is None:
            self._ctx = OpenQuoteContext(host=OPEND_HOST, port=OPEND_PORT)
        return self._ctx

    def _close(self) -> None:
        if self._ctx:
            self._ctx.close()
            self._ctx = None

    def _rate_limit(self) -> None:
        """Ensure we don't exceed rate limit."""
        self._request_count += 1
        elapsed = time.time() - self._window_start
        if elapsed < 60 and self._request_count >= self.rate_limit_per_min:
            wait = 60 - elapsed + 1
            logger.info(f"Rate limit: waiting {wait:.0f}s")
            time.sleep(wait)
            self._window_start = time.time()
            self._request_count = 0
        elif elapsed >= 60:
            self._window_start = time.time()
            self._request_count = 0

    def call_api(self, method_name: str, *args: object, **kwargs: object):
        self._rate_limit()
        ctx = self._get_ctx()
        method = getattr(ctx, method_name)
        ret, data = method(*args, **kwargs)
        if ret != RET_OK:
            logger.error(f"{method_name} failed: {data}")
            return None
        return data

    def get_symbols(self) -> list[str]:
        """Get list of symbols to collect. Override in subclasses."""
        from google.cloud import bigquery

        client = bigquery.Client()
        table = f"quant.{self.market}_bars_5m"
        rows = client.query(
            f"SELECT DISTINCT symbol FROM `deductive-notch-495015-c2.{table}` "
            f"WHERE timestamp >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 5 DAY)"
        ).result()
        return sorted(r.symbol for r in rows)

    def collect_one(self, symbol: str) -> pd.DataFrame | None:
        """Collect data for a single symbol. Override in subclasses."""
        raise NotImplementedError

    def collect_all(self) -> pd.DataFrame:
        """Iterate over symbols and collect data."""
        symbols = self.get_symbols()
        all_data = []
        for i, sym in enumerate(symbols):
            try:
                df = self.collect_one(sym)
                if df is not None and not (hasattr(df, "empty") and df.empty):
                    df_copy = df.copy()
                    df_copy["symbol"] = sym
                    all_data.append(df_copy)
                if (i + 1) % 50 == 0:
                    logger.info(f"Progress: {i + 1}/{len(symbols)}")
            except Exception as e:
                logger.warning(f"Failed {sym}: {e}")
        self._close()
        if not all_data:
            logger.warning(f"No data collected for {self.market}")
            return pd.DataFrame()
        result = pd.concat(all_data, ignore_index=True)
        logger.info(f"Collected {len(result)} rows from {len(symbols)} symbols")
        return result

    def save_to_gcs(self, df: pd.DataFrame, table_name: str, date_str: str | None = None) -> str | None:
        """Save DataFrame to GCS as Parquet."""
        if df.empty:
            logger.info(f"No data to save for {table_name}")
            return None

        if date_str is None:
            date_str = datetime.now(UTC).strftime("%Y-%m-%d")

        gcs_path = f"gs://{GCS_BUCKET}/{self.market}/{table_name}/{date_str}/data.parquet"
        df.to_parquet(gcs_path, index=False)
        logger.info(f"Saved {len(df)} rows to {gcs_path}")
        return gcs_path
