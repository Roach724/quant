"""Futu capital flow adapter — capital distribution data.

NOTE: get_capital_flow() returns -1 for US stocks (HK-only).
This adapter uses get_capital_distribution() which works for both US and HK.
"""
import pandas as pd
from futu import RET_OK

from collectors.adapters._futu_base import FutuBaseAdapter


class FutuCapitalFlowAdapter(FutuBaseAdapter):
    DATA_TYPE = "capital_flow"

    def _call_api(self, symbol: str):
        ctx = self._get_ctx()
        self._rate_limit()
        ret, data = ctx.get_capital_distribution(symbol)
        return data if ret == RET_OK and isinstance(data, pd.DataFrame) else pd.DataFrame()

    def _parse(self, symbol: str, raw) -> pd.DataFrame:
        return raw if isinstance(raw, pd.DataFrame) else pd.DataFrame()
