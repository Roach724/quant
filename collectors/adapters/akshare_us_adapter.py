"""US stock market adapter via akshare (fallback for yfinance)."""
from datetime import date, time, timezone
import logging

import pandas as pd

try:
    import akshare as ak
except ImportError:
    ak = None

logger = logging.getLogger(__name__)

# Common US market prefixes for akshare stock_us_hist
# 105=NASDAQ, 106=NYSE, 107=AMEX
_AKSHARE_US_PREFIXES = ["105", "106", "107"]


class AkshareUSAdapter:
    """US stock data adapter using akshare stock_us_hist.

    Used as a fallback when yfinance returns empty or insufficient data.
    Only supports daily frequency; minute-level data is out of scope.

    Accepts plain symbols (e.g. "MSFT") and automatically tries common
    akshare prefixes (105, 106, 107) to find the correct eastmoney secid.
    """

    market = "US"

    def fetch_bars(
        self,
        symbols: list[str],
        start: date | None = None,
        end: date | None = None,
        frequency: str = "1d",
    ) -> pd.DataFrame:
        """Fetch daily OHLCV bars for US stocks via akshare.

        Args:
            symbols: List of US stock symbols (plain, e.g. ["MSFT", "AAPL"]).
            start: Start date (inclusive).
            end: End date (inclusive).
            frequency: Bar frequency. Only "1d" is supported.

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
            # If symbol already has akshare format (e.g. "105.MSFT"), use as-is
            if "." in sym:
                raw = self._try_fetch_hist(sym, start_str, end_str)
                if raw is not None:
                    self._extract_records(raw, sym.split(".")[-1], frequency, all_records)
                continue

            # Plain symbol: try common prefixes
            fetched = False
            for prefix in _AKSHARE_US_PREFIXES:
                akshare_sym = f"{prefix}.{sym}"
                raw = self._try_fetch_hist(akshare_sym, start_str, end_str)
                if raw is not None:
                    self._extract_records(raw, sym, frequency, all_records)
                    fetched = True
                    break

            if not fetched:
                logger.debug("akshare: no data for symbol %s with any prefix", sym)

        return pd.DataFrame(all_records) if all_records else empty_df

    @staticmethod
    def _try_fetch_hist(symbol: str, start_str: str, end_str: str) -> pd.DataFrame | None:
        """Try fetching history for a single akshare symbol. Returns None on failure."""
        try:
            raw = ak.stock_us_hist(
                symbol=symbol,
                period="daily",
                start_date=start_str,
                end_date=end_str,
                adjust="",
            )
        except Exception:
            return None

        if raw is None or raw.empty:
            return None
        return raw

    @staticmethod
    def _extract_records(
        raw: pd.DataFrame,
        symbol: str,
        frequency: str,
        all_records: list,
    ) -> None:
        """Extract standard OHLCV records from akshare raw DataFrame."""
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
            return

        raw["timestamp"] = pd.to_datetime(raw["timestamp"])
        raw = raw.dropna(subset=["open", "close"])

        for _, row in raw.iterrows():
            ts = row["timestamp"]
            # akshare returns naive dates (market-close dates in US Eastern)
            if ts.tzinfo is None:
                ts = ts.tz_localize("America/New_York").tz_convert("UTC")
            else:
                ts = ts.tz_convert("UTC")

            all_records.append({
                "symbol": symbol,
                "timestamp": ts,
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
                "volume": int(float(row["volume"])),
                "market": "US",
                "frequency": frequency,
            })

    def fetch_supported_symbols(self) -> list[str]:
        """Return empty list — symbol management is external (via YFinanceUSAdapter.fetch_all_symbols)."""
        return []

    def market_hours(self, d: date) -> tuple[time, time]:
        """Return US market open/close hours in Eastern time."""
        return time(9, 30), time(16, 0)
