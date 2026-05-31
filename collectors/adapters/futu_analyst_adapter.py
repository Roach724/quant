"""Futu analyst consensus adapter — ratings and target prices.

API returns flat dict: {highest, average, lowest, rating, total, buy, hold, sell, update_time}.
"""
import pandas as pd
from futu import RET_OK
from collectors.adapters._futu_base import FutuBaseAdapter


class FutuAnalystAdapter(FutuBaseAdapter):
    DATA_TYPE = "analyst"

    def _call_api(self, symbol: str):
        ctx = self._get_ctx()
        self._rate_limit()
        ret, data = ctx.get_research_analyst_consensus(symbol)
        return data if ret == RET_OK and isinstance(data, dict) else {}

    def _parse(self, symbol: str, raw: dict) -> pd.DataFrame:
        if not raw:
            return pd.DataFrame()
        return self._dict_to_dataframe(raw)
