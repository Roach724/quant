"""Futu OpenD cryptocurrency market adapter — LV1."""

import logging
import os
from datetime import date, datetime, time

import pandas as pd
from futu import (
    RET_OK,
    AuType,
    KLType,
    OpenQuoteContext,
)

logger = logging.getLogger(__name__)


class CryptoFutuAdapter:
    """Futu OpenD cryptocurrency market adapter.

    Uses request_history_kline with pagination.
    Symbol format conversion: 'BTC/USDT' → 'CC.BTCUSD'.
    Crypto has no forward adjustment — uses AuType.NONE.
    """

    market = "CRYPTO"

    _SUPPORTED_SYMBOLS = [
        "BTC/USDT", "ETH/USDT", "SOL/USDT", "LTC/USDT",
        "XRP/USDT", "DOT/USDT", "ADA/USDT", "AVAX/USDT",
        "LINK/USDT", "UNI/USDT",
    ]

    _FREQ_MAP = {
        "1m": KLType.K_1M,
        "5m": KLType.K_5M,
        "15m": KLType.K_15M,
        "30m": KLType.K_30M,
        "1h": KLType.K_60M,
        "1d": KLType.K_DAY,
        "1w": KLType.K_WEEK,
    }

    def __init__(self, host: str | None = None, port: int | None = None):
        self.host = host or os.environ.get("OPEND_HOST", "127.0.0.1")
        self.port = port or int(os.environ.get("OPEND_PORT", "11111"))
        self._ctx: OpenQuoteContext | None = None

    def _get_ctx(self) -> OpenQuoteContext:
        if self._ctx is None:
            self._ctx = OpenQuoteContext(host=self.host, port=self.port)
        return self._ctx

    def _to_futu_code(self, symbol: str) -> str:
        """Convert 'BTC/USDT' → 'CC.BTCUSD'"""
        base = symbol.split("/")[0]
        return f"CC.{base}USD"

    def _map_frequency(self, frequency: str):
        mapped = self._FREQ_MAP.get(frequency)
        if mapped is None:
            raise ValueError(f"Unsupported frequency: {frequency}")
        return mapped

    def fetch_bars(
        self,
        symbols: list[str],
        start: datetime,
        end: datetime,
        frequency: str = "1m",
    ) -> pd.DataFrame:
        """Fetch OHLCV bars via request_history_kline with pagination.

        Args:
            symbols: ["BTC/USDT", "ETH/USDT"] internal format
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
        for sym in symbols:
            futu_code = self._to_futu_code(sym)
            page_key = None

            while True:
                ret, data, page_key = ctx.request_history_kline(
                    futu_code, start=start_str, end=end_str,
                    ktype=ktype, autype=AuType.NONE,
                    max_count=1000, page_req_key=page_key,
                )
                if ret != RET_OK:
                    logger.warning("Futu crypto fetch failed for %s: %s", sym, data)
                    break

                for _, row in data.iterrows():
                    records.append({
                        "symbol": sym,
                        "timestamp": row["time_key"],
                        "open": float(row["open"]),
                        "high": float(row["high"]),
                        "low": float(row["low"]),
                        "close": float(row["close"]),
                        "volume": int(float(row["volume"])),
                        "market": self.market,
                    })

                if page_key is None:
                    break

        return pd.DataFrame(records)

    def fetch_supported_symbols(self) -> list[str]:
        """Return list of supported crypto symbols in internal format."""
        return list(self._SUPPORTED_SYMBOLS)

    def market_hours(self, d: date) -> tuple[time, time]:
        """Crypto is 24/7."""
        return (time(0, 0), time(23, 59, 59))

    def close(self):
        """Close the OpenD context."""
        if self._ctx is not None:
            try:
                self._ctx.close()
            except Exception:
                pass
            self._ctx = None
