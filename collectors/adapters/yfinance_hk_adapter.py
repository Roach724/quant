"""Hong Kong stock market adapter via yfinance."""
from datetime import time

import pandas as pd
import yfinance as yf


class YFinanceHKAdapter:
    market = "HK"

    _HK_SYMBOLS = [
        # Tech / Internet
        "0700.HK", "9988.HK", "3690.HK", "9618.HK", "9999.HK",
        "9888.HK", "2015.HK", "9868.HK", "1810.HK", "1024.HK", "9626.HK",
        # Finance / Property
        "0005.HK", "0388.HK", "1299.HK", "2318.HK", "3968.HK",
        "1398.HK", "3988.HK", "2628.HK", "0011.HK",
        # Conglomerates / Energy / Consumer
        "0001.HK", "0002.HK", "0003.HK", "0016.HK", "0027.HK",
        "0175.HK", "0267.HK", "0291.HK", "0669.HK", "0823.HK",
        "0883.HK", "0941.HK", "1044.HK", "1093.HK", "1177.HK",
        "1928.HK", "2269.HK",
    ]

    _FREQ_MAP = {"1m": "1m", "5m": "5m", "15m": "15m", "1h": "1h", "1d": "1d"}

    def __init__(self):
        pass

    def fetch_bars(self, symbols, start, end, frequency="1d"):
        yf_interval = self._FREQ_MAP.get(frequency, "1d")
        tickers = yf.Tickers(" ".join(symbols))
        df = tickers.history(start=start, end=end, interval=yf_interval)

        if df.empty:
            return pd.DataFrame(columns=[
                "symbol", "timestamp", "open", "high", "low", "close",
                "volume", "market", "frequency",
            ])

        records = []
        for symbol in symbols:
            clean_sym = symbol.replace(".HK", "")
            if symbol not in df.columns.get_level_values(1):
                continue
            sym_df = df.xs(symbol, level=1, axis=1).dropna(subset=["Open"])
            for ts, row in sym_df.iterrows():
                records.append({
                    "symbol": clean_sym,
                    "timestamp": ts.tz_convert("UTC") if ts.tzinfo else ts.tz_localize("UTC"),
                    "open": float(row["Open"]),
                    "high": float(row["High"]),
                    "low": float(row["Low"]),
                    "close": float(row["Close"]),
                    "volume": int(row["Volume"]),
                    "market": self.market,
                    "frequency": frequency,
                })
        return pd.DataFrame(records)

    def fetch_supported_symbols(self):
        return [s.replace(".HK", "") for s in self._HK_SYMBOLS]

    def market_hours(self, d):
        return time(9, 30), time(16, 0)
