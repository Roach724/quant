"""Futu shareholder adapter — holding changes + institutional holdings.

API returns standard (ret, DataFrame) for both endpoints.
Works for both US and HK.
"""
import pandas as pd
from futu import RET_OK
from collectors.adapters._futu_base import FutuBaseAdapter


class FutuShareholderAdapter(FutuBaseAdapter):
    DATA_TYPE = "shareholder"

    def _call_api(self, symbol: str):
        ctx = self._get_ctx()
        frames: list[pd.DataFrame] = []

        self._rate_limit()
        ret, data = ctx.get_shareholders_holding_changes(symbol, num=20)
        if ret == RET_OK and isinstance(data, pd.DataFrame) and len(data) > 0:
            data["data_type"] = "holding_changes"
            frames.append(data)

        self._rate_limit()
        ret, data = ctx.get_shareholders_institutional(symbol, num=20)
        if ret == RET_OK and isinstance(data, pd.DataFrame) and len(data) > 0:
            data["data_type"] = "institutional"
            frames.append(data)

        return frames

    def _parse(self, symbol: str, raw: list) -> pd.DataFrame:
        if not raw:
            return pd.DataFrame()
        return pd.concat(raw, ignore_index=True)
