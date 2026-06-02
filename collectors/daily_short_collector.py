"""Collect daily short volume from Futu API."""
import pandas as pd
from collectors.futu_collector_base import FutuCollector
from futu import RET_OK

class DailyShortCollector(FutuCollector):
    def collect_one(self, symbol: str) -> pd.DataFrame:
        self._rate_limit()
        ctx = self._get_ctx()
        ret, page_key, data = ctx.get_daily_short_volume(symbol)
        if ret != RET_OK:
            return None
        if data is None or (hasattr(data, 'empty') and data.empty):
            return None
        return data
