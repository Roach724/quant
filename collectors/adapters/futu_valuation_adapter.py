"""Futu valuation adapter — PE/PB/PS trends and distributions.

API returns nested dict: {valuation_type, trend: {date: value}, market_distribution: {...}, ...}
We flatten trend dicts into (date, value) rows.
"""
import pandas as pd
from futu import RET_OK

from collectors.adapters._futu_base import FutuBaseAdapter

VALUATION_TYPES = {"pe": 1, "pb": 2, "ps": 3}
INTERVAL_TYPES = {3: "1y", 4: "3y", 6: "5y"}


class FutuValuationAdapter(FutuBaseAdapter):
    DATA_TYPE = "valuation"

    def _call_api(self, symbol: str):
        """Fetch all valuation type × interval combinations."""
        ctx = self._get_ctx()
        results: dict[str, dict] = {}
        for vt_name, vt_id in VALUATION_TYPES.items():
            for int_id, int_label in INTERVAL_TYPES.items():
                self._rate_limit()
                ret, data = ctx.get_valuation_detail(
                    symbol, valuation_type=vt_id, interval_type=int_id,
                )
                if ret == RET_OK and isinstance(data, dict):
                    results[f"{vt_name}_{int_label}"] = data
        return results

    def _parse(self, symbol: str, raw: dict) -> pd.DataFrame:
        """Flatten nested valuation dict → DataFrame.

        Trend dict has both scalar keys (current_value, avg_minus_1_stddev, ...)
        AND a historical_items list of {value, time, time_str, plate_value} dicts.
        Expand historical_items into individual rows.
        """
        rows: list[dict] = []
        for key, data in raw.items():
            vt, interval = key.split("_", 1)
            trend = data.get("trend", {})

            # Historical time series — expand list of dicts
            hist = trend.get("historical_items", [])
            if isinstance(hist, list):
                for item in hist:
                    if isinstance(item, dict):
                        rows.append({
                            "valuation_type": vt, "interval": interval,
                            "date": item.get("time_str", ""),
                            "value": item.get("value"),
                            "plate_value": item.get("plate_value"),
                        })

            # Scalar statistics — single row
            scalar_keys = {"current_value", "average_value", "avg_minus_1_stddev",
                           "avg_plus_1_stddev", "avg_minus_2_stddev",
                           "avg_plus_2_stddev", "median_value"}
            for sk in scalar_keys:
                if sk in trend and isinstance(trend[sk], (int, float)):
                    rows.append({
                        "valuation_type": vt, "interval": interval,
                        "date": sk, "value": trend[sk],
                    })

        return pd.DataFrame(rows) if rows else pd.DataFrame()
