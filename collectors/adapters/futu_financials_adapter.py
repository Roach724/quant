"""Futu financial statements adapter.

API returns (ret, dict) where dict = {next_key, structure_list, report_list}.
report_list[0] = header row (column names)
report_list[1:] = data rows
    Each data row has an item_list at position 9:
    [{field_id, display_name, data, yoy, qoq}, ...]

We expand item_list into individual rows.
"""

import pandas as pd
from futu import RET_OK

from collectors.adapters._futu_base import FutuBaseAdapter

STATEMENT_TYPES = {"income": 1, "balance_sheet": 2, "cash_flow": 3, "main_index": 4}

# field_id -> human-readable name (Futu standard IDs)
FIELD_ID_NAMES = {
    8001: "revenue",  # 营业收入
    8002: "total_revenue",  # 营业额
    8003: "cost_of_revenue",  # 营业成本
    8004: "gross_profit",  # 毛利
    8005: "operating_income",  # 营业利润
    8007: "pretax_income",  # 税前利润
    8010: "net_income",  # 净利润
    8017: "total_assets",  # 资产总计
    8022: "current_assets",  # 流动资产
    8033: "total_liabilities",  # 负债合计
    8034: "current_liabilities",  # 流动负债
    8035: "shareholder_equity",  # 股东权益
    8037: "operating_cash_flow",  # 经营活动现金流
    8038: "investing_cash_flow",  # 投资活动现金流
    8043: "financing_cash_flow",  # 筹资活动现金流
    8046: "free_cash_flow",  # 自由现金流
    8047: "eps",  # 每股收益
    8048: "diluted_eps",  # 稀释每股收益
    8049: "dps",  # 每股股息
}


class FutuFinancialsAdapter(FutuBaseAdapter):
    DATA_TYPE = "financials"

    def __init__(self, host=None, port=None, symbols=None, financial_type=10):
        super().__init__(host=host, port=port, symbols=symbols)
        self.financial_type = financial_type

    def _call_api(self, symbol: str):
        ctx = self._get_ctx()
        all_rows: list[dict] = []
        for stype_name, stype_id in STATEMENT_TYPES.items():
            next_key = None
            while True:
                self._rate_limit()
                ret, data = ctx.get_financials_statements(
                    symbol,
                    statement_type=stype_id,
                    financial_type=self.financial_type,
                    next_key=next_key,
                    num=50,
                )
                if ret != RET_OK or not isinstance(data, dict):
                    break
                rl = data.get("report_list", [])
                if rl and len(rl) >= 2:
                    # rl[0] = header row dict with keys like date_time, ..., item_list
                    _header = rl[0]
                    for row in rl[1:]:
                        item_list = row.get("item_list", [])
                        if isinstance(item_list, list):
                            for item in item_list:
                                field_id = item.get("field_id", 0)
                                all_rows.append(
                                    {
                                        "statement_type": stype_name,
                                        "fiscal_year": row.get("fiscal_year"),
                                        "period_text": row.get("period_text", ""),
                                        "date_time_str": row.get("date_time_str", ""),
                                        "financial_type": row.get("financial_type"),
                                        "currency_code": row.get("currency_code", ""),
                                        "accounting_standards": row.get("accounting_standards", ""),
                                        "field_id": field_id,
                                        "field_name": FIELD_ID_NAMES.get(field_id, f"field_{field_id}"),
                                        "data": item.get("data"),
                                        "yoy": item.get("yoy"),
                                        "qoq": item.get("qoq"),
                                    }
                                )
                nk = data.get("next_key", "-1")
                if not nk or nk == "-1":
                    break
                next_key = nk
        return pd.DataFrame(all_rows)

    def _parse(self, symbol: str, raw) -> pd.DataFrame:
        return raw if isinstance(raw, pd.DataFrame) else pd.DataFrame()
