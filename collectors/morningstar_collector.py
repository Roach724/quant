"""Collect Morningstar research reports from Futu API."""

import pandas as pd

from collectors.futu_collector_base import FutuCollector


class MorningstarCollector(FutuCollector):
    """Flattens Morningstar dict response into a single-row DataFrame."""

    def collect_one(self, symbol: str) -> pd.DataFrame | None:
        data = self.call_api("get_research_morningstar_report", symbol)
        if data is None:
            return None
        # data is a dict with ~20 keys — flatten to 1 row
        row: dict = {}
        for k, v in data.items():
            if v is None:
                row[k] = None
            elif isinstance(v, (int, float, str, bool)):
                row[k] = v
            elif isinstance(v, list):
                row[k] = len(v)  # count entries (e.g. bull_say, bear_say)
            elif isinstance(v, dict):
                # Extract label or context text
                row[k] = str(v.get("label") or v.get("context", "") or "")[:500]
            else:
                row[k] = str(v)[:500]
        return pd.DataFrame([row])
