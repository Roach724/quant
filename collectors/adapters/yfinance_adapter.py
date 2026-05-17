from datetime import date, datetime, time
import logging

import pandas as pd
import yfinance as yf

logger = logging.getLogger(__name__)


class YFinanceUSAdapter:
    market = "US"

    # Minimal fallback list used when dynamic discovery fails
    _FALLBACK_SYMBOLS = [
        "AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "META", "TSLA", "BRK-B",
        "JPM", "V", "JNJ", "WMT", "PG", "MA", "UNH", "HD", "DIS", "BAC",
        "PYPL", "ADBE", "CRM", "NFLX", "INTC", "CSCO", "VZ", "PFE", "MRK",
        "ABT", "KO", "PEP", "TMO", "NKE", "ORCL", "ABBV", "ACN", "AVGO",
        "COST", "CVX", "MCD", "WFC", "TXN", "QCOM", "AMD", "AMGN", "HON",
        "INTU", "IBM", "PM", "MS", "LOW", "CAT", "SPY",
    ]

    def __init__(self, fallback_adapter=None):
        """Initialize the US adapter.

        Args:
            fallback_adapter: Optional adapter (e.g. AkshareUSAdapter) used when
                              yfinance returns empty or insufficient data.
        """
        self._fallback = fallback_adapter

    def fetch_bars(
        self,
        symbols: list[str],
        start: datetime,
        end: datetime,
        frequency: str = "1m",
    ) -> pd.DataFrame:
        """Fetch OHLCV bars, falling back to akshare if yfinance returns too few rows.

        Args:
            symbols: List of US stock symbols, e.g. ["AAPL", "MSFT"].
            start: Start datetime.
            end: End datetime.
            frequency: Bar interval ("1d", "5m", etc.).

        Returns:
            DataFrame with standard OHLCV columns.
        """
        empty_df = pd.DataFrame(columns=[
            "symbol", "timestamp", "open", "high", "low", "close",
            "volume", "market", "frequency",
        ])

        # --- Primary: yfinance ---
        df = self._fetch_yfinance(symbols, start, end, frequency)

        if df is not None and len(df) >= 5:
            return df

        # --- Fallback: akshare (only for 1d) ---
        if self._fallback is not None and frequency == "1d":
            logger.info("yfinance returned %d rows (<5), falling back to akshare",
                        len(df) if df is not None else 0)
            try:
                df_fb = self._fallback.fetch_bars(symbols, start, end, frequency)
                if df_fb is not None and not df_fb.empty:
                    return df_fb
            except Exception as e:
                logger.warning("akshare fallback also failed: %s", e)

        return df if df is not None else empty_df

    def _fetch_yfinance(
        self,
        symbols: list[str],
        start: datetime,
        end: datetime,
        frequency: str = "1m",
    ) -> pd.DataFrame | None:
        """Internal: fetch from yfinance only."""
        valid_intervals = {"1m": "1m", "5m": "5m", "15m": "15m", "1h": "1h", "1d": "1d"}
        yf_interval = valid_intervals.get(frequency, "1m")

        try:
            tickers = yf.Tickers(" ".join(symbols))
            df = tickers.history(start=start, end=end, interval=yf_interval)
        except Exception as e:
            logger.warning("yfinance fetch failed: %s", e)
            return None

        if df.empty:
            return None

        records = []
        for symbol in symbols:
            if symbol not in df.columns.get_level_values(1):
                continue
            sym_df = df.xs(symbol, level=1, axis=1).dropna(subset=["Open"])
            for ts, row in sym_df.iterrows():
                records.append({
                    "symbol": symbol,
                    "timestamp": ts.tz_convert("UTC") if ts.tzinfo else ts.tz_localize("UTC"),
                    "open": float(row["Open"]),
                    "high": float(row["High"]),
                    "low": float(row["Low"]),
                    "close": float(row["Close"]),
                    "volume": int(row["Volume"]),
                    "market": self.market,
                    "frequency": frequency,
                })

        return pd.DataFrame(records) if records else None

    @staticmethod
    def fetch_all_symbols() -> list[str]:
        """Dynamically fetch all eligible US stock symbols.

        Tries multiple approaches in order:
        1. akshare stock_us_spot_em() — full US stock list, filtered by
           price and volume for top ~500 liquid stocks.
        2. Wikipedia S&P 500 constituents table.
        Falls back to a built-in list if all dynamic methods fail.

        Returns:
            List of plain US stock symbols, e.g. ["AAPL", "MSFT", ...].
        """
        # Approach 1: akshare full US stock list
        try:
            import akshare as ak

            raw = ak.stock_us_spot_em()
            if raw is not None and not raw.empty:
                # akshare columns: 序号, 名称, 最新价, 涨跌额, 涨跌幅, 开盘价, 最高价, 最低价,
                #                  昨收价, 总市值, 市盈率, 成交量, 成交额, 振幅, 换手率, 代码
                # "代码" is in format "105.MSFT"
                if "代码" not in raw.columns:
                    raise ValueError("unexpected akshare columns: %s" % raw.columns.tolist())

                df = raw.copy()
                # Extract plain symbol from akshare code ("105.MSFT" → "MSFT")
                df["plain_symbol"] = df["代码"].astype(str).str.split(".").str[-1].str.upper()
                df["plain_symbol"] = df["plain_symbol"].str.strip()

                # Filter: only valid US ticker patterns (1-5 uppercase letters)
                df = df[df["plain_symbol"].str.match(r'^[A-Z0-9\.\-]{1,10}$')]

                # Price filter (>= $1)
                if "最新价" in df.columns:
                    df["最新价"] = pd.to_numeric(df["最新价"], errors="coerce")
                    df = df[df["最新价"] >= 1.0]

                # Volume filter (> 0)
                if "成交量" in df.columns:
                    df["成交量"] = pd.to_numeric(df["成交量"], errors="coerce")
                    df = df[df["成交量"] > 0]

                # Sort by volume descending, take top 500
                if "成交量" in df.columns:
                    df = df.sort_values("成交量", ascending=False)

                symbols = df["plain_symbol"].head(500).unique().tolist()
                if symbols:
                    logger.info("fetch_all_symbols: got %d US symbols via akshare", len(symbols))
                    return symbols
        except Exception as e:
            logger.debug("akshare stock_us_spot_em failed: %s", e)

        # Approach 2: Wikipedia S&P 500
        try:
            tables = pd.read_html("https://en.wikipedia.org/wiki/List_of_S%26P_500_companies")
            if tables:
                sp_df = tables[0]
                symbols = sp_df["Symbol"].astype(str).str.replace(".", "-", regex=False).tolist()
                symbols = list(dict.fromkeys(symbols))  # deduplicate
                logger.info("fetch_all_symbols: got %d symbols from Wikipedia S&P 500", len(symbols))
                return symbols
        except Exception as e:
            logger.debug("Wikipedia S&P 500 fetch failed: %s", e)

        # Fallback: built-in list
        logger.warning("fetch_all_symbols: all dynamic methods failed, using built-in list")
        return list(YFinanceUSAdapter._FALLBACK_SYMBOLS)

    def fetch_supported_symbols(self) -> list[str]:
        """Return full stock pool (dynamically fetched, not hardcoded)."""
        return self.fetch_all_symbols()

    def market_hours(self, d: date) -> tuple[time, time]:
        """Return US market open/close hours in Eastern time."""
        return time(9, 30), time(16, 0)
