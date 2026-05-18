from typing import Protocol
import pandas as pd


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
