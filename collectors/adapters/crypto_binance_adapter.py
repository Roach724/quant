"""Binance cryptocurrency market adapter via ccxt."""

from datetime import UTC, datetime, time

import ccxt
import pandas as pd


class CryptoBinanceAdapter:
    market = "CRYPTO"

    _TOP_SYMBOLS = [
        "BTC/USDT",
        "ETH/USDT",
        "SOL/USDT",
        "BNB/USDT",
        "XRP/USDT",
        "DOGE/USDT",
        "ADA/USDT",
        "AVAX/USDT",
        "DOT/USDT",
        "LINK/USDT",
        "MATIC/USDT",
        "UNI/USDT",
        "ATOM/USDT",
        "LTC/USDT",
        "ETC/USDT",
        "APT/USDT",
        "ARB/USDT",
        "OP/USDT",
        "NEAR/USDT",
        "FIL/USDT",
    ]

    _FREQ_MAP = {
        "1m": "1m",
        "5m": "5m",
        "15m": "15m",
        "30m": "30m",
        "1h": "1h",
        "4h": "4h",
        "1d": "1d",
        "1w": "1w",
    }

    def __init__(self):
        self._exchange = ccxt.binance({"enableRateLimit": True})

    def fetch_bars(self, symbols, start, end, frequency="1m"):
        """Fetch OHLCV bars from Binance, normalizing to Bar schema."""
        tf = self._FREQ_MAP.get(frequency, "1m")
        since_ms = int(start.timestamp() * 1000)
        end_ms = int(end.timestamp() * 1000)
        limit = 1000
        records = []

        for symbol in symbols:
            current_since = since_ms
            while current_since < end_ms:
                try:
                    ohlcv = self._exchange.fetch_ohlcv(symbol, tf, current_since, limit)
                except Exception:
                    break
                if not ohlcv:
                    break
                for row in ohlcv:
                    ts_ms = row[0]
                    if ts_ms >= end_ms:
                        break
                    if ts_ms >= since_ms:
                        records.append(
                            {
                                "symbol": symbol.replace("/", ""),
                                "timestamp": datetime.fromtimestamp(ts_ms / 1000, tz=UTC),
                                "open": float(row[1]),
                                "high": float(row[2]),
                                "low": float(row[3]),
                                "close": float(row[4]),
                                "volume": int(float(row[5])),
                                "market": self.market,
                                "frequency": frequency,
                            }
                        )
                last_ts = ohlcv[-1][0]
                if last_ts <= current_since:
                    break
                current_since = last_ts + 1

        if not records:
            return pd.DataFrame(
                columns=[
                    "symbol",
                    "timestamp",
                    "open",
                    "high",
                    "low",
                    "close",
                    "volume",
                    "market",
                    "frequency",
                ]
            )
        return pd.DataFrame(records)

    def fetch_supported_symbols(self):
        return [s.replace("/", "") for s in self._TOP_SYMBOLS]

    def market_hours(self, d):
        return time(0, 0), time(23, 59)
