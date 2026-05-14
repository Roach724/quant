from datetime import date, datetime, time

import pandas as pd
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame, TimeFrameUnit


class AlpacaUSAdapter:
    market = "US"

    def __init__(self, api_key: str, api_secret: str):
        self._client = StockHistoricalDataClient(api_key, api_secret)

    def fetch_bars(
        self,
        symbols: list[str],
        start: datetime,
        end: datetime,
        frequency: str = "1m",
    ) -> pd.DataFrame:
        freq_map = {
            "1m": TimeFrame(1, TimeFrameUnit.Minute),
            "5m": TimeFrame(5, TimeFrameUnit.Minute),
            "15m": TimeFrame(15, TimeFrameUnit.Minute),
            "1h": TimeFrame(1, TimeFrameUnit.Hour),
            "1d": TimeFrame(1, TimeFrameUnit.Day),
        }
        tf = freq_map.get(frequency, freq_map["1m"])

        request = StockBarsRequest(
            symbol_or_symbols=symbols,
            timeframe=tf,
            start=start,
            end=end,
        )
        response = self._client.get_stock_bars(request)

        records = []
        for symbol, bars in response.data.items():
            for bar in bars:
                records.append({
                    "symbol": symbol,
                    "timestamp": bar.timestamp.replace(tzinfo=None),
                    "open": bar.open,
                    "high": bar.high,
                    "low": bar.low,
                    "close": bar.close,
                    "volume": bar.volume,
                    "market": self.market,
                    "frequency": frequency,
                })

        return pd.DataFrame(records)

    def fetch_supported_symbols(self) -> list[str]:
        from alpaca.data.requests import StockLatestBarRequest
        response = self._client.get_stock_latest_bar(
            StockLatestBarRequest(symbol_or_symbols=[])
        )
        return sorted(response.data.keys())

    def market_hours(self, d: date) -> tuple[time, time]:
        return time(9, 30), time(16, 0)
