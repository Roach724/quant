"""Futu OpenD stock market adapter — HK (LV2) + US (LV3) equities."""

import os
import logging
from datetime import date, time, datetime
from typing import Optional

import pandas as pd
from futu import (
    OpenQuoteContext, RET_OK, AuType, KLType,
)

logger = logging.getLogger(__name__)


class FutuStockAdapter:
    """Futu OpenD stock market adapter for HK + US equities.

    Uses request_history_kline with pagination.
    Supports both HK (LV2) and US (LV3: NasBasic+TotalView+Arcabook).
    """

    market = "MIXED"

    _FREQ_MAP = {
        "1m": KLType.K_1M,
        "5m": KLType.K_5M,
        "15m": KLType.K_15M,
        "30m": KLType.K_30M,
        "1h": KLType.K_60M,
        "1d": KLType.K_DAY,
        "1w": KLType.K_WEEK,
    }

    def __init__(self, host: Optional[str] = None, port: Optional[int] = None):
        self.host = host or os.environ.get("OPEND_HOST", "127.0.0.1")
        self.port = port or int(os.environ.get("OPEND_PORT", "11111"))
        self._ctx: Optional[OpenQuoteContext] = None

    def _get_ctx(self) -> OpenQuoteContext:
        if self._ctx is None:
            self._ctx = OpenQuoteContext(host=self.host, port=self.port)
        return self._ctx

    def _map_frequency(self, frequency: str):
        mapped = self._FREQ_MAP.get(frequency)
        if mapped is None:
            raise ValueError(f"Unsupported frequency: {frequency}")
        return mapped

    def _determine_autype(self, code: str) -> int:
        """HK stocks use QFQ (forward-adjusted), US uses NONE."""
        if code.startswith("HK."):
            return AuType.QFQ
        return AuType.NONE

    def fetch_bars(
        self,
        symbols: list[str],
        start: datetime,
        end: datetime,
        frequency: str = "1d",
    ) -> pd.DataFrame:
        """Fetch OHLCV bars via request_history_kline with pagination.

        Args:
            symbols: ["HK.00700", "HK.09988", "US.AAPL"] format
            start: start datetime
            end: end datetime
            frequency: "1m", "5m", "1d", etc.

        Returns:
            DataFrame with columns: symbol, timestamp, open, high, low,
            close, volume, market
        """
        ctx = self._get_ctx()
        ktype = self._map_frequency(frequency)
        start_str = start.strftime("%Y-%m-%d")
        end_str = end.strftime("%Y-%m-%d")

        records = []
        for code in symbols:
            autype = self._determine_autype(code)
            page_key = None

            while True:
                ret, data, page_key = ctx.request_history_kline(
                    code, start=start_str, end=end_str,
                    ktype=ktype, autype=autype,
                    max_count=1000, page_req_key=page_key,
                )
                if ret != RET_OK:
                    logger.warning("Futu fetch failed for %s: %s", code, data)
                    break

                for _, row in data.iterrows():
                    records.append({
                        "symbol": code,
                        "timestamp": row["time_key"],
                        "open": float(row["open"]),
                        "high": float(row["high"]),
                        "low": float(row["low"]),
                        "close": float(row["close"]),
                        "volume": int(float(row["volume"])),
                        "market": "HK" if code.startswith("HK.") else "US",
                    })

                if page_key is None:
                    break

        return pd.DataFrame(records)

    def fetch_supported_symbols(self) -> list[str]:
        """Return known symbols from Futu.

        TODO: Implement get_plate_stock for HK dynamic symbol discovery.
        """
        return []

    def market_hours(self, d: date) -> tuple[time, time]:
        """Return trading hours.
        
        HK: 09:30-16:00, US: 09:30-16:00 ET
        TODO: Use get_market_state for dynamic hours.
        """
        return (time(9, 30), time(16, 0))

    def close(self):
        """Close the OpenD context."""
        if self._ctx is not None:
            try:
                self._ctx.close()
            except Exception:
                pass
            self._ctx = None
