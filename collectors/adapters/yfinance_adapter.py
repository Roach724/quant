from datetime import date, datetime, time

import pandas as pd
import yfinance as yf


class YFinanceUSAdapter:
    market = "US"

    _SP500_SYMBOLS = [
        "AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "META", "TSLA", "BRK-B",
        "JPM", "V", "JNJ", "WMT", "PG", "MA", "UNH", "HD", "DIS", "BAC",
        "PYPL", "ADBE", "CRM", "NFLX", "INTC", "CSCO", "VZ", "PFE", "MRK",
        "ABT", "KO", "PEP", "TMO", "NKE", "ORCL", "ABBV", "ACN", "AVGO",
        "COST", "CVX", "MCD", "WFC", "TXN", "QCOM", "AMD", "AMGN", "HON",
        "INTU", "IBM", "PM", "MS", "LOW", "CAT", "SPY",
    ]

    def __init__(self):
        pass

    def fetch_bars(
        self,
        symbols: list[str],
        start: datetime,
        end: datetime,
        frequency: str = "1m",
    ) -> pd.DataFrame:
        valid_intervals = {"1m": "1m", "5m": "5m", "15m": "15m", "1h": "1h", "1d": "1d"}
        yf_interval = valid_intervals.get(frequency, "1m")

        tickers = yf.Tickers(" ".join(symbols))
        df = tickers.history(start=start, end=end, interval=yf_interval)

        if df.empty:
            return pd.DataFrame(columns=[
                "symbol", "timestamp", "open", "high", "low", "close", "volume", "market", "frequency"
            ])

        records = []
        for symbol in symbols:
            if symbol not in df.columns.get_level_values(1):
                continue
            sym_df = df.xs(symbol, level=1, axis=1).dropna(subset=["Open"])
            for ts, row in sym_df.iterrows():
                records.append({
                    "symbol": symbol,
                    "timestamp": ts.tz_convert("UTC"),
                    "open": float(row["Open"]),
                    "high": float(row["High"]),
                    "low": float(row["Low"]),
                    "close": float(row["Close"]),
                    "volume": int(row["Volume"]),
                    "market": self.market,
                    "frequency": frequency,
                })

        return pd.DataFrame(records)

    def fetch_supported_symbols(self) -> list[str]:
        return list(self._SP500_SYMBOLS)

    def market_hours(self, d: date) -> tuple[time, time]:
        return time(9, 30), time(16, 0)
