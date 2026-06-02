"""Collect insider trade and holder data from Futu API (US only)."""
import pandas as pd
from collectors.futu_collector_base import FutuCollector


class InsiderTradeCollector(FutuCollector):
    """Collects insider trading records (SEC Form 4)."""
    def collect_one(self, symbol: str) -> pd.DataFrame:
        data = self.call_api("get_insider_trade_list", symbol)
        if data is None:
            return None
        if hasattr(data, 'empty') and data.empty:
            return None
        return data


class InsiderHolderCollector(FutuCollector):
    """Collects insider holding summaries."""
    def collect_one(self, symbol: str) -> pd.DataFrame:
        data = self.call_api("get_insider_holder_list", symbol)
        if data is None:
            return None
        if hasattr(data, 'empty') and data.empty:
            return None
        return data
