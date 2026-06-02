"""Collect capital distribution (大中小单资金分布) from Futu API."""
import pandas as pd
from collectors.futu_collector_base import FutuCollector

class CapitalDistributionCollector(FutuCollector):
    def collect_one(self, symbol: str) -> pd.DataFrame:
        data = self.call_api("get_capital_distribution", symbol)
        if data is None:
            return None
        return data
