"""Hong Kong stock market adapter via yfinance with akshare fallback."""
import logging
from datetime import date, time

import pandas as pd
import yfinance as yf

logger = logging.getLogger(__name__)


class YFinanceHKAdapter:
    market = "HK"

    _FREQ_MAP = {"1m": "1m", "5m": "5m", "15m": "15m", "1h": "1h", "1d": "1d"}

    # Built-in fallback list used when akshare is unavailable
    _FALLBACK_SYMBOLS = [
        "0700", "9988", "3690", "9618", "9999", "9888", "2015", "9868", "1810", "1024", "9626",
        "0005", "0388", "1299", "2318", "3968", "1398", "3988", "2628", "0011",
        "0001", "0002", "0003", "0016", "0027", "0175", "0267", "0291", "0669", "0823",
        "0883", "0941", "1044", "1093", "1177", "1928", "2269",
    ]

    def __init__(self, fallback_adapter=None):
        """Initialize the HK adapter.

        Args:
            fallback_adapter: Optional adapter (e.g. AkshareHKAdapter) used when
                              yfinance returns empty or insufficient data.
        """
        self._fallback = fallback_adapter

    def fetch_bars(
        self,
        symbols: list[str],
        start: date,
        end: date,
        frequency: str = "1d",
    ) -> pd.DataFrame:
        """Fetch OHLCV bars, falling back to akshare if yfinance returns too few rows.

        Args:
            symbols: List of HK stock codes with .HK suffix, e.g. ["0700.HK", "9988.HK"].
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
            fallback_symbols = [s.replace(".HK", "").zfill(5) for s in symbols]
            try:
                df_fb = self._fallback.fetch_bars(fallback_symbols, start, end, frequency)
                if df_fb is not None and not df_fb.empty:
                    return df_fb
            except Exception as e:
                logger.warning("akshare fallback also failed: %s", e)

        return df if df is not None else empty_df

    def _fetch_yfinance(
        self,
        symbols: list[str],
        start: date,
        end: date,
        frequency: str = "1d",
    ) -> pd.DataFrame | None:
        """Internal: fetch from yfinance only."""
        yf_interval = self._FREQ_MAP.get(frequency, "1d")
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

        return pd.DataFrame(records) if records else None

    @staticmethod
    def fetch_all_symbols() -> list[str]:
        """Dynamically fetch all eligible HK stock symbols via akshare.

        Multi-level fallback:
          1. config/stock_pool_hk.csv (pre-cached snapshot of 港股通)
          2. akshare stock_hk_ggt_components_em() with liquidity filtering
          3. Built-in _FALLBACK_SYMBOLS list (~37 major stocks)

        Returns:
            List of 4-digit symbol strings (no .HK suffix), e.g. ["0700", "9988", ...].
        """
        import os

        import pandas as pd

        # Priority 1: pre-cached CSV snapshot
        csv_path = os.path.normpath(os.path.join(
            os.path.dirname(__file__), "..", "..", "config", "stock_pool_hk.csv"
        ))
        if os.path.exists(csv_path):
            try:
                df = pd.read_csv(csv_path, header=None, names=["symbol"])
                symbols = sorted(
                    df["symbol"].astype(str).str.strip().str.zfill(4).tolist()
                )
                if len(symbols) > 100:
                    logger.info(
                        "fetch_all_symbols: loaded %d symbols from %s",
                        len(symbols), csv_path,
                    )
                    return symbols
            except Exception as e:
                logger.debug("fetch_all_symbols: CSV failed (%s), trying akshare", e)

        # Priority 2: live akshare API
        try:
            import akshare as ak

            raw = ak.stock_hk_ggt_components_em()
            if raw is None or raw.empty:
                raise ValueError("akshare returned empty dataframe")

            # Standardize columns
            df = raw.rename(columns={
                "代码": "symbol",
                "名称": "name",
                "最新价": "price",
                "成交量": "volume",
                "成交额": "turnover",
            })
            df["symbol"] = df["symbol"].astype(str).str.lstrip("0")
            df["symbol"] = df["symbol"].apply(lambda s: s if s else "0")
            df["symbol"] = df["symbol"].str.zfill(4)
            df["price"] = pd.to_numeric(df["price"], errors="coerce")
            df["turnover"] = pd.to_numeric(df["turnover"], errors="coerce")

            # Apply filters
            df = df[df["price"] >= 1.0]
            df = df[df["turnover"] >= 1_000_000]

            symbols = sorted(df["symbol"].unique().tolist())
            logger.info("fetch_all_symbols: got %d HK symbols (after filters)", len(symbols))
            return symbols

        except Exception as e:
            logger.warning(
                "fetch_all_symbols via akshare failed (%s), using built-in fallback list", e
            )
            return list(YFinanceHKAdapter._FALLBACK_SYMBOLS)

    def fetch_supported_symbols(self) -> list[str]:
        """Return full stock pool (dynamically fetched, not hardcoded)."""
        return self.fetch_all_symbols()

    def market_hours(self, d: date) -> tuple[time, time]:
        """Return HK market open/close hours in local time."""
        return time(9, 30), time(16, 0)
