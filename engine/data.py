from typing import Protocol, Optional
import logging
import pandas as pd
from google.cloud import bigquery

log = logging.getLogger(__name__)


class DataSource(Protocol):
    universe: list[str]
    close: pd.DataFrame
    open: pd.DataFrame | None
    high: pd.DataFrame | None
    low: pd.DataFrame | None
    volume: pd.DataFrame | None
    timestamp: pd.DatetimeIndex

    def iloc(self, i: int) -> dict: ...
    def __len__(self) -> int: ...


class DataFrameSource:
    """Wraps a pre-loaded DataFrame as a DataSource for the engine."""
    def __init__(self, close, open=None, high=None, low=None, volume=None, pred=None):
        self.close = close
        self.open = open if open is not None else close.copy()
        self.high = high if high is not None else close.copy()
        self.low = low if low is not None else close.copy()
        self.volume = volume if volume is not None else pd.DataFrame(1, index=close.index, columns=close.columns)
        self.pred = pred
        self.universe = list(close.columns)
        self.timestamp = close.index

    def iloc(self, i):
        row = {"close": {}}
        for col in self.universe:
            row["close"][col] = self.close.iloc[i][col]
        # add all OHLCV fields
        for field in ("open", "high", "low", "volume"):
            df = getattr(self, field, None)
            if df is not None:
                row[field] = {}
                for col in self.universe:
                    if col in df.columns:
                        row[field][col] = df.iloc[i][col]
        # add prediction if available
        if self.pred is not None:
            row["pred"] = {}
            for col in self.universe:
                if col in self.pred.columns:
                    row["pred"][col] = self.pred.iloc[i][col]
        return row

    def __len__(self):
        return len(self.close)


class BigQuery5mSource:
    """Loads 5-minute bars from BigQuery for backtesting.
    
    Parameters
    ----------
    market : str
        Market code, e.g. 'us', 'hk'. Used in table name: quant.{market}_bars_5m.
    project : str, optional
        GCP project ID. Defaults to the default project from the environment.
    start : str
        Start date, e.g. '2026-05-01'.
    end : str
        End date, e.g. '2026-05-29'.
    symbols : list[str], optional
        List of symbol codes. If provided, symbols() returns these directly.
        Otherwise, symbols() queries DISTINCT symbols from BQ for the date range.
    """
    def __init__(self, market='us', project=None, start=None, end=None, symbols=None):
        self.market = market.lower()
        self.project = project
        self.start = start
        self.end = end
        self._symbols = symbols
        self._client = bigquery.Client(project=project) if project else bigquery.Client()
        self._len = None

    @property
    def table(self) -> str:
        """Fully-qualified BigQuery table name."""
        proj = self.project or self._client.project
        return f'{proj}.quant.{self.market}_bars_5m'

    def symbols(self) -> list[str]:
        """Return the list of symbols.
        
        Uses self._symbols if provided at init; otherwise queries BQ for
        DISTINCT symbols in the date range.
        """
        if self._symbols is not None:
            return self._symbols
        query = f"""
            SELECT DISTINCT symbol
            FROM `{self.table}`
            WHERE DATE(timestamp) BETWEEN @start AND @end
            ORDER BY symbol
        """
        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("start", "STRING", self.start),
                bigquery.ScalarQueryParameter("end", "STRING", self.end),
            ]
        )
        rows = self._client.query(query, job_config=job_config).result()
        return [row.symbol for row in rows]

    def load_all(self) -> "DataFrameSource":
        """Load all symbols as wide-format DataFrameSource for PaperRunner.
        
        Queries BigQuery for all symbols in the configured date range,
        pivots to wide format (columns=symbols, index=timestamp), and
        returns a DataFrameSource with close, open, high, low, volume.
        """
        syms = self.symbols()
        if not syms:
            raise ValueError(f"No symbols found in {self.table} for {self.start}→{self.end}")

        query = f"""
            SELECT symbol, timestamp, open, high, low, close, volume
            FROM `{self.table}`
            WHERE symbol IN UNNEST(@symbols)
              AND timestamp BETWEEN @start AND @end
            ORDER BY timestamp, symbol
        """
        job_config = bigquery.QueryJobConfig(query_parameters=[
            bigquery.ArrayQueryParameter("symbols", "STRING", syms),
            bigquery.ScalarQueryParameter("start", "STRING", self.start),
            bigquery.ScalarQueryParameter("end", "STRING", self.end),
        ])
        df = self._client.query(query, job_config=job_config).to_dataframe()
        df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
        # Strip US. prefix for PaperRunner/strategy compatibility
        df["symbol"] = df["symbol"].str.replace("US.", "", regex=False)

        close = df.pivot_table(index="timestamp", columns="symbol", values="close").ffill()
        open_df = df.pivot_table(index="timestamp", columns="symbol", values="open").ffill()
        high = df.pivot_table(index="timestamp", columns="symbol", values="high").ffill()
        low = df.pivot_table(index="timestamp", columns="symbol", values="low").ffill()
        volume = df.pivot_table(index="timestamp", columns="symbol", values="volume").fillna(0)

        log.info("BQ 5m data: %d bars x %d symbols", len(close), len(syms))
        return DataFrameSource(close=close, open=open_df, high=high, low=low, volume=volume)

    def load_bars(self, symbol: str) -> pd.DataFrame:
        """Query 5-minute bars for a single symbol.
        
        Returns a DataFrame with timestamp as DatetimeIndex and columns:
        open, high, low, close, volume.
        """
        query = f"""
            SELECT timestamp, open, high, low, close, volume
            FROM `{self.table}`
            WHERE symbol = @symbol
              AND DATE(timestamp) BETWEEN @start AND @end
            ORDER BY timestamp
        """
        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("symbol", "STRING", symbol),
                bigquery.ScalarQueryParameter("start", "STRING", self.start),
                bigquery.ScalarQueryParameter("end", "STRING", self.end),
            ]
        )
        df = self._client.query(query, job_config=job_config).to_dataframe()
        if df.empty:
            return df
        df['timestamp'] = pd.to_datetime(df['timestamp'])
        df = df.set_index('timestamp').sort_index()
        return df

    def __len__(self) -> int:
        """Return approximate bar count for the symbol set and date range."""
        if self._len is not None:
            return self._len
        syms = self.symbols()
        if not syms:
            return 0
        # Estimate total bars: sample one symbol first
        query = f"""
            SELECT COUNT(*) AS cnt
            FROM `{self.table}`
            WHERE symbol = @symbol
              AND DATE(timestamp) BETWEEN @start AND @end
        """
        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("symbol", "STRING", syms[0]),
                bigquery.ScalarQueryParameter("start", "STRING", self.start),
                bigquery.ScalarQueryParameter("end", "STRING", self.end),
            ]
        )
        row = next(self._client.query(query, job_config=job_config).result())
        self._len = row.cnt * len(syms)
        return self._len
