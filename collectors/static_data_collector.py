"""Collect static/semi-static data from Futu API."""

import pandas as pd

from collectors.futu_collector_base import FutuCollector


class OwnerPlateCollector(FutuCollector):
    """Which plates/industries a stock belongs to."""

    def collect_one(self, symbol: str) -> pd.DataFrame | None:
        data = self.call_api("get_owner_plate", [symbol])
        if data is None:
            return None
        if hasattr(data, "empty") and data.empty:
            return None
        return data  # type: ignore[no-any-return]


class RehabCollector(FutuCollector):
    """Rehabilitation/adjustment factors (dividends, splits)."""

    def get_symbols(self) -> list[str]:
        from google.cloud import bigquery

        client = bigquery.Client()
        rows = client.query(
            f"SELECT DISTINCT symbol FROM `deductive-notch-495015-c2.quant.{self.market}_bars_1d`"
        ).result()
        return sorted(r.symbol for r in rows)

    def collect_one(self, symbol: str) -> pd.DataFrame | None:
        data = self.call_api("get_rehab", symbol)
        if data is None:
            return None
        if hasattr(data, "empty") and data.empty:
            return None
        return data  # type: ignore[no-any-return]


class TopTenBrokersCollector(FutuCollector):
    """HK top 10 buy/sell brokers (HK only)."""

    def __init__(self) -> None:
        super().__init__(market="hk")

    def collect_one(self, symbol: str) -> pd.DataFrame | None:
        data = self.call_api("get_top_ten_buy_sell_brokers", symbol)
        if data is None:
            return None
        if hasattr(data, "empty") and data.empty:
            return None
        return data  # type: ignore[no-any-return]
