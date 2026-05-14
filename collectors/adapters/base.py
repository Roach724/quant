# collectors/adapters/base.py
from datetime import date, datetime, time
from typing import Protocol, runtime_checkable

import pandas as pd


@runtime_checkable
class MarketAdapter(Protocol):
    market: str

    def fetch_bars(
        self,
        symbols: list[str],
        start: datetime,
        end: datetime,
        frequency: str = "1m",
    ) -> pd.DataFrame: ...

    def fetch_supported_symbols(self) -> list[str]: ...

    def market_hours(self, d: date) -> tuple[time, time]: ...
