"""Hong Kong stock market adapter via akshare (fallback for yfinance)."""
from datetime import date, time, timezone

import pandas as pd

try:
    import akshare as ak
except ImportError:
    ak = None  # deferred ImportError when actually used


class AkshareHKAdapter:
    """HK stock data adapter using akshare stock_hk_hist.
    
    Used as a fallback when yfinance returns empty or insufficient data.
    Only supports daily frequency; minute-level data is out of scope.
    """
    market = "HK"

    def fetch_bars(
        self,
        symbols: list[str],
        start: date | None = None,
        end: date | None = None,
        frequency: str = "1d",
    ) -> pd.DataFrame:
        """Fetch daily OHLCV bars for HK stocks via akshare.

        Args:
            symbols: List of HK stock codes in akshare 5-digit format, e.g. ["00700", "09988"].
            start: Start date (inclusive).
            end: End date (inclusive).
            frequency: Bar frequency. Only "1d" is supported; others return an empty DataFrame.

        Returns:
            DataFrame with columns: symbol, timestamp, open, high, low, close,
            volume, market, frequency. All timestamps in UTC.
        """
        empty_df = pd.DataFrame(columns=[
            "symbol", "timestamp", "open", "high", "low", "close",
            "volume", "market", "frequency",
        ])

        if frequency != "1d":
            return empty_df

        if ak is None:
            raise ImportError("akshare is not installed. Run: pip install akshare")

        all_records = []
        if isinstance(start, str):
            start = pd.Timestamp(start).to_pydatetime()
        if isinstance(end, str):
            end = pd.Timestamp(end).to_pydatetime()

        start_str = start.strftime("%Y%m%d") if start else "20000101"
        end_str = end.strftime("%Y%m%d") if end else date.today().strftime("%Y%m%d")

        for sym in symbols:
            try:
                raw = ak.stock_hk_hist(
                    symbol=sym,
                    period="daily",
                    start_date=start_str,
                    end_date=end_str,
                    adjust="",
                )
            except Exception:
                continue

            if raw is None or raw.empty:
                continue

            # Expected akshare columns: 日期, 开盘, 收盘, 最高, 最低, 成交量, 成交额, 振幅, 涨跌幅, 涨跌额, 换手率
            col_map = {
                "日期": "timestamp",
                "开盘": "open",
                "最高": "high",
                "最低": "low",
                "收盘": "close",
                "成交量": "volume",
            }

            raw = raw.rename(columns=col_map)
            needed = ["timestamp", "open", "high", "low", "close", "volume"]
            missing = [c for c in needed if c not in raw.columns]
            if missing:
                continue

            raw["timestamp"] = pd.to_datetime(raw["timestamp"])
            raw = raw.dropna(subset=["open", "close"])

            for _, row in raw.iterrows():
                ts = row["timestamp"]
                if ts.tzinfo is None:
                    ts = ts.tz_localize("Asia/Hong_Kong").tz_convert("UTC")
                else:
                    ts = ts.tz_convert("UTC")

                all_records.append({
                    "symbol": sym,
                    "timestamp": ts,
                    "open": float(row["open"]),
                    "high": float(row["high"]),
                    "low": float(row["low"]),
                    "close": float(row["close"]),
                    "volume": int(row["volume"]),
                    "market": self.market,
                    "frequency": frequency,
                })

        return pd.DataFrame(all_records) if all_records else empty_df

    def fetch_supported_symbols(self) -> list[str]:
        """Return empty list — symbol management is external (via YFinanceHKAdapter.fetch_all_symbols)."""
        return []

    def market_hours(self, d: date) -> tuple[time, time]:
        """Return HK market open/close hours in local time."""
        return time(9, 30), time(16, 0)
