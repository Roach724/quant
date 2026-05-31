"""Futu financial statements adapter.

API returns (ret, dict) where dict = {next_key, structure_list, report_list}.
structure_list: [{name, type, ...}, ...] — column definitions
report_list: [[val1, val2, ...], ...] — data rows
"""
import pandas as pd
from futu import RET_OK
from collectors.adapters._futu_base import FutuBaseAdapter

STATEMENT_TYPES = {"income": 1, "balance_sheet": 2, "cash_flow": 3, "main_index": 4}


class FutuFinancialsAdapter(FutuBaseAdapter):
    DATA_TYPE = "financials"

    def __init__(self, host=None, port=None, symbols=None, financial_type=10):
        super().__init__(host=host, port=port, symbols=symbols)
        self.financial_type = financial_type

    def _call_api(self, symbol: str):
        """Fetch all 4 statement types, paginate with next_key."""
        ctx = self._get_ctx()
        all_rows: list[dict] = []
        for stype_name, stype_id in STATEMENT_TYPES.items():
            next_key = None
            while True:
                self._rate_limit()
                ret, data = ctx.get_financials_statements(
                    symbol, statement_type=stype_id,
                    financial_type=self.financial_type, next_key=next_key, num=50,
                )
                if ret != RET_OK or not isinstance(data, dict):
                    break
                sl = data.get("structure_list", [])
                rl = data.get("report_list", [])
                if sl and rl:
                    cols = [s.get("name", f"c{i}") for i, s in enumerate(sl)]
                    for row in rl:
                        all_rows.append(dict(zip(cols, row)))
                nk = data.get("next_key", "-1")
                if not nk or nk == "-1":
                    break
                next_key = nk

        if not all_rows:
            return pd.DataFrame()
        return pd.DataFrame(all_rows).assign(statement_type=stype_name)

    def _parse(self, symbol: str, raw) -> pd.DataFrame:
        return raw if isinstance(raw, pd.DataFrame) else pd.DataFrame()
