"""Collect earnings-related data from Futu API."""

import pandas as pd

from collectors.futu_collector_base import FutuCollector


class EarningsPriceMoveCollector(FutuCollector):
    """Collects earnings-day price move (前/后 N 日涨跌幅)."""

    def get_symbols(self) -> list[str]:
        from google.cloud import bigquery

        client = bigquery.Client()
        rows = client.query(
            f"SELECT DISTINCT symbol FROM `deductive-notch-495015-c2.quant.{self.market}_bars_1d` "
            "WHERE timestamp >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 730 DAY)"
        ).result()
        return sorted(r.symbol for r in rows)

    def collect_one(self, symbol: str) -> pd.DataFrame | None:
        data = self.call_api("get_financials_earnings_price_move", f"{self.market.upper()}.{symbol}", period_count=10)
        if data is None:
            return None
        if hasattr(data, "empty") and data.empty:
            return None
        return data  # type: ignore[no-any-return]


class EarningsPriceHistoryCollector(FutuCollector):
    """Collects earnings-day detailed price history."""

    def get_symbols(self) -> list[str]:
        from google.cloud import bigquery

        client = bigquery.Client()
        rows = client.query(
            f"SELECT DISTINCT symbol FROM `deductive-notch-495015-c2.quant.{self.market}_bars_1d` "
            "WHERE timestamp >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 730 DAY)"
        ).result()
        return sorted(r.symbol for r in rows)

    def collect_one(self, symbol: str) -> pd.DataFrame | None:
        data = self.call_api("get_financials_earnings_price_history", f"{self.market.upper()}.{symbol}")
        if data is None:
            return None
        if hasattr(data, "empty") and data.empty:
            return None
        return data  # type: ignore[no-any-return]
