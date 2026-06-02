"""BQ Polling Data Source — queries BigQuery us_bars_5m on a timer.

Uses existing ws_collector → GCS → BQ loader pipeline. Requires the
BQ loader to run frequently during market hours (every 5-10 min) so that
completed 5m bars are available with acceptable latency.

Supports multi-day runs: calling run() after a previous run() exited
(at market close) will resume polling with the same last_ts watermark.

Usage:
    source = BQDataSource(symbols=["AAPL", "MSFT", ...], market="us")
    source.on_bar = lambda bar: strategy.on_bar(bar)
    source.run()  # blocking until market close, stop(), or stop_check
    # Next day:
    source.run()  # resumes — remembers last_ts from previous day
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from zoneinfo import ZoneInfo

import pandas as pd
from google.cloud import bigquery

from live.calendar import MarketCalendar

logger = logging.getLogger(__name__)

# BQ bars are stored in exchange local time (OpenD returns local-time timestamps
# and the collector writes them as-is). Use IANA timezone names for DST correctness.
_MARKET_TZ: dict[str, ZoneInfo] = {}


def _get_market_tz(market: str) -> ZoneInfo:
    """Return market-local ZoneInfo, with lazy init to avoid importing at module level."""
    if market not in _MARKET_TZ:
        if market == "hk":
            _MARKET_TZ[market] = ZoneInfo("Asia/Hong_Kong")
        else:
            _MARKET_TZ[market] = ZoneInfo("America/New_York")  # US (EDT/EST auto)
    return _MARKET_TZ[market]


class BQDataSource:
    """Polls BigQuery us_bars_5m on a timer for real-time bar data.

    No OpenD subscription required — uses existing data pipeline.
    Requires frequent BQ loader cron during market hours.

    Restartable: call run() again after market-close stop — last_ts
    watermark is preserved across runs.
    """

    def __init__(
        self,
        symbols: list[str],
        market: str = "us",
        poll_interval_sec: int = 60,
        project: str = "deductive-notch-495015-c2",
        stop_check: Callable[[], bool] | None = None,
    ):
        self.symbols = symbols
        self.market = market
        self.poll_interval = poll_interval_sec
        self.project = project
        self._running = False
        self._last_ts: str | None = None
        self._client: bigquery.Client | None = None
        self.on_bar: Callable[[dict], None] | None = None
        self.stop_check = stop_check  # external stop condition
        self.failure_count: int = 0  # consecutive poll failures
        self._calendar = MarketCalendar(market)

    @staticmethod
    def _compute_seed(market: str) -> str:
        """Return seed timestamp = today's market open in market-local time string.

        BQ bars are stored with market-local timestamps (ET for US, HKT for HK)
        labeled as UTC TIMESTAMP. The seed must use the same convention so
        string comparisons work correctly in BigQuery.
        """
        from datetime import datetime as dt
        from datetime import timedelta as td

        tz = _get_market_tz(market)
        now_local = dt.now(tz)

        # Market open hours in LOCAL market time
        _local_open: dict[str, tuple[int, int]] = {
            "us": (9, 30),
            "hk": (9, 30),
        }
        open_h, open_m = _local_open.get(market, (now_local.hour, 0))
        today_open = now_local.replace(hour=open_h, minute=open_m, second=0, microsecond=0)

        if now_local >= today_open:
            seed = today_open
        else:
            seed = today_open - td(days=1)

        # Return as plain string (no tz suffix) — BQ treats as UTC
        return seed.strftime("%Y-%m-%d %H:%M:%S")

    def run(self):
        """Blocking loop — polls BQ until market close, stop(), or stop_check.

        Can be called multiple times for multi-day runs:
        - First call: seeds last_ts from ~1 hour ago, creates BQ client.
        - Subsequent calls: reuses last_ts and creates fresh BQ client.
        """
        self._running = True

        # Create fresh BQ client (old one may have expired)
        if self._client is None:
            self._client = bigquery.Client(project=self.project)

        # Seed last_ts on first run only; preserve across multi-day runs.
        # BQ bars are stored as UTC TIMESTAMP — seed in UTC, not market tz.
        if self._last_ts is None:
            self._last_ts = self._compute_seed(self.market)

        logger.info(
            "BQDataSource: polling every %ds — %d symbols, last_ts=%s",
            self.poll_interval,
            len(self.symbols),
            self._last_ts,
        )
        try:
            while self._running and self._calendar.is_open_now():
                if self.stop_check and self.stop_check():
                    logger.info("BQDataSource: stop_check returned True — stopping")
                    break
                try:
                    self._poll()
                    self.failure_count = 0  # reset on success
                except Exception:
                    self.failure_count += 1
                    logger.exception("BQDataSource: poll failed (#%d)", self.failure_count)
                time.sleep(self.poll_interval)
        except KeyboardInterrupt:
            logger.info("BQDataSource: interrupted")
        finally:
            # Keep _client and _last_ts for potential restart
            logger.info("BQDataSource: stopped (market closed or stop requested)")

    def stop(self):
        self._running = False

    def reset(self):
        """Fully reset internal state — clears last_ts and failure count."""
        self._last_ts = None
        self.failure_count = 0
        self._client = None

    def is_connected(self) -> bool:
        return self._client is not None

    @property
    def last_ts(self) -> str | None:
        """Last seen timestamp (for state persistence)."""
        return self._last_ts

    @last_ts.setter
    def last_ts(self, value: str | None):
        self._last_ts = value

    @property
    def calendar(self) -> MarketCalendar:
        """Access the market calendar (for is_open_now, time_until_open, etc.)."""
        return self._calendar

    # ── internals ──

    def _poll(self):
        """Query BQ for new bars since last_ts."""
        if self._client is None:
            return

        table = (
            "us_bars_5m"
            if self.market == "us"
            else ("hk_bars_5m" if self.market == "hk" else "crypto_bars_5m")
        )

        # BQ bars use exchange-prefixed symbols (US.AAPL, HK.00001),
        # while the experiment uses unprefixed symbols (AAPL, 00001).
        # Prefix symbols for the query, strip prefix in the callback.
        # HK symbols must be zero-padded to 5 digits (e.g., 0005 → HK.00005).
        _pre = self.market.upper() + "."
        query_symbols = []
        for s in self.symbols:
            bare = (
                s.replace(_pre, "").lstrip("0").zfill(5)
                if self.market == "hk"
                else s.replace(_pre, "")
            )  # noqa: E501
            query_symbols.append(_pre + bare)

        query = f"""
            SELECT symbol, timestamp, open, high, low, close, volume
            FROM `{self.project}.quant.{table}`
            WHERE symbol IN UNNEST(@symbols)
              AND timestamp > @last_ts
            ORDER BY timestamp
        """
        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ArrayQueryParameter("symbols", "STRING", query_symbols),
                bigquery.ScalarQueryParameter("last_ts", "STRING", self._last_ts),
            ],
        )
        try:
            df = self._client.query(query, job_config=job_config).to_dataframe()
        except Exception:
            logger.exception("BQDataSource: query failed")
            return

        logger.info("BQDataSource: _poll returned %d rows", len(df))
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
                "timestamp": ts,  # keep as datetime for downstream consumers
            }
            for _, row in group.iterrows():
                sym = row["symbol"]
                # Strip exchange prefix (US.AAPL → AAPL, HK.00005 → 00005)
                if "." in sym:
                    sym = sym.split(".", 1)[1]
                # Normalize: strip leading zeros (HK 00005 → 5)
                sym = sym.lstrip("0")
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
