"""Futu short interest adapter — short positions + daily short volume.

NOTE: get_short_interest returns 3 values (ret, df1, df2), not the usual 2!
The 3rd value is an aggregated_short DataFrame which may be empty.
"""

import pandas as pd
from futu import RET_OK

from collectors.adapters._futu_base import FutuBaseAdapter


class FutuShortInterestAdapter(FutuBaseAdapter):
    DATA_TYPE = "short_interest"

    def _call_api(self, symbol: str):
        ctx = self._get_ctx()
        frames: list[pd.DataFrame] = []

        # Short interest — 3 return values!
        self._rate_limit()
        result = ctx.get_short_interest(symbol, num=20)
        ret = result[0] if isinstance(result, tuple) else result
        if ret == RET_OK:
            si_df = result[1] if len(result) > 1 else pd.DataFrame()
            agg_df = result[2] if len(result) > 2 else pd.DataFrame()
            if isinstance(si_df, pd.DataFrame) and len(si_df) > 0:
                si_df["data_type"] = "short_interest"
                frames.append(si_df)
            if isinstance(agg_df, pd.DataFrame) and len(agg_df) > 0:
                agg_df["data_type"] = "aggregated_short"
                frames.append(agg_df)

        # Daily short volume — standard 2-value return
        self._rate_limit()
        ret, data = ctx.get_daily_short_volume(symbol, num=20)
        if ret == RET_OK and isinstance(data, pd.DataFrame) and len(data) > 0:
            data["data_type"] = "daily_short_volume"
            frames.append(data)

        return frames

    def _parse(self, symbol: str, raw: list) -> pd.DataFrame:
        if not raw:
            return pd.DataFrame()
        return pd.concat(raw, ignore_index=True)
