"""BQ Polling Data Source — queries BigQuery us_bars_5m on a timer.

Uses existing ws_collector → GCS → BQ loader pipeline. Requires the
BQ loader to run frequently during market hours (every 5-10 min) so that
completed 5m bars are available with acceptable latency.

Usage:
    source = BQDataSource(symbols=["AAPL", "MSFT", ...], market="us")
    source.on_bar = lambda bar: strategy.on_bar(bar)
    source.run()  # blocking until market close or stop()
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone, timedelta
from typing import Callable

import pandas as pd
from google.cloud import bigquery

logger = logging.getLogger(__name__)

_MARKET_HOURS = {
    "us": {"open": (13, 30), "close": (20, 0)},
    "hk": {"open": (1, 30), "close": (8, 0),
           "lunch_start": (4, 0), "lunch_end": (5, 0)},
}


class BQDataSource:
    """Polls BigQuery us_bars_5m on a timer for real-time bar data.

    No OpenD subscription required — uses existing data pipeline.
    Requires frequent BQ loader cron during market hours.
    """

    def __init__(
        self,
        symbols: list[str],
        market: str = "us",
        poll_interval_sec: int = 60,
        project: str = "deductive-notch-495015-c2",
    ):
        self.symbols = symbols
        self.market = market
        self.poll_interval = poll_interval_sec
        self.project = project
        self._running = False
        self._last_ts: str | None = None
        self._client: bigquery.Client | None = None
        self.on_bar: Callable[[dict], None] | None = None

    def run(self):
        """Blocking loop — polls BQ until market close or stop()."""
        self._running = True
        self._client = bigquery.Client(project=self.project)

        # Seed last_ts to "now - 1 hour" to avoid loading all history
        seed = datetime.now(timezone.utc) - timedelta(hours=1)
        self._last_ts = seed.strftime("%Y-%m-%d %H:%M:%S")

        logger.info(
            "BQDataSource: polling every %ds — %d symbols, last_ts=%s",
            self.poll_interval, len(self.symbols), self._last_ts,
        )
        try:
            while self._running and self._is_market_open():
                try:
                    self._poll()
                except Exception:
                    logger.exception("BQDataSource: poll failed")
                time.sleep(self.poll_interval)
        except KeyboardInterrupt:
            logger.info("BQDataSource: interrupted")
        finally:
            self._client = None
            logger.info("BQDataSource: stopped")

    def stop(self):
        self._running = False

    def is_connected(self) -> bool:
        return self._client is not None

    # ── internals ──

    def _poll(self):
        """Query BQ for new bars since last_ts."""
        if self._client is None:
            return

        table = "us_bars_5m" if self.market == "us" else (
            "hk_bars_5m" if self.market == "hk" else "crypto_bars_5m"
        )
        sym_filter = ", ".join(f"'{s}'" for s in self.symbols)

        query = f"""
            SELECT symbol, timestamp, open, high, low, close, volume
            FROM `{self.project}.quant.{table}`
            WHERE symbol IN UNNEST(@symbols)
              AND timestamp > @last_ts
            ORDER BY timestamp
        """
        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ArrayQueryParameter("symbols", "STRING", self.symbols),
                bigquery.ScalarQueryParameter("last_ts", "STRING", self._last_ts),
            ],
        )
        try:
            df = self._client.query(query, job_config=job_config).to_dataframe()
        except Exception:
            logger.exception("BQDataSource: query failed")
            return

        if df.empty:
            return

        # Update last_ts to latest timestamp
        latest = str(df["timestamp"].max())
        if latest > (self._last_ts or ""):
            self._last_ts = latest

        # Feed each unique timestamp's bars as a batch
        df["timestamp"] = pd.to_datetime(df["timestamp"])
        for ts, group in df.groupby("timestamp"):
            bar_data = {
                "close": {},
                "open": {},
                "high": {},
                "low": {},
                "volume": {},
                "timestamp": str(ts),
            }
            for _, row in group.iterrows():
                sym = row["symbol"]
                bar_data["close"][sym] = float(row["close"])
                bar_data["open"][sym] = float(row["open"])
                bar_data["high"][sym] = float(row["high"])
                bar_data["low"][sym] = float(row["low"])
                bar_data["volume"][sym] = float(row["volume"])

            if self.on_bar:
                try:
                    self.on_bar(bar_data)
                except Exception:
                    logger.exception("BQDataSource: on_bar callback failed")

            logger.debug("BQDataSource: bar @ %s — %d symbols", ts, len(group))

    def _is_market_open(self) -> bool:
        hours = _MARKET_HOURS.get(self.market)
        if not hours:
            return True
        now = datetime.now(timezone.utc)
        if now.weekday() >= 5:
            return False
        t = now.time()
        import datetime as _dt
        open_t = _dt.time(*hours["open"])
        close_t = _dt.time(*hours["close"])
        if "lunch_start" in hours:
            ls = _dt.time(*hours["lunch_start"])
            le = _dt.time(*hours["lunch_end"])
            if ls <= t < le:
                return False
        return open_t <= t <= close_t
