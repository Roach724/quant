"""Collect stock screening data (multi-factor) via Futu StockScreen V2 API (ProtoID 3252).

Uses the StockScreenRequest high-level builder to query the API in pages,
extracting a configurable set of factors as a flat DataFrame.

Factor categories supported:
  - Basic: CODE (1101), NAME (1102)
  - Simple: PRICE (2201), MARKET_CAP (2301), PE_TTM (2303), PB (2304), VOLUME_RATIO (2217)
  - Cumulative: PRICE_CHANGE_PCT (3102) for 1W/1M/3M, TURNOVER_RATIO (3106)
  - Financial: ROE (4110), ROA_TTM (4209), GROSS_PROFIT_RATIO (4108), PS_TTM (4904)
  - Indicator: MA5/MA20/MA60/RSI_14/KDJ_K/KDJ_D/KDJ_J/BOLL_UPPER/BOLL_MID/BOLL_LOWER
  - Featured: Capital flow total/main net inflow for 1D and 5D
"""

from __future__ import annotations

import logging
from typing import Any, Callable

import pandas as pd
from futu import OpenQuoteContext, RET_OK, StockScreenRequest
from futu.quote.stock_screen_const import (
    BasicProperty,
    CashFlowPeriod,
    CumulativeProperty,
    FeaturedProperty,
    FinancialProperty,
    Indicator,
    Period,
    ScrMarket,
    SimpleField,
    SimpleProperty,
    Term,
)

from collectors.futu_collector_base import FutuCollector

logger = logging.getLogger(__name__)

# ── Retrieve field definitions ──────────────────────────────────────────
# Each entry: (builder_method, params_dict, column_name)
# params_dict is passed as **kwargs to the builder method.
_RETRIEVE_DEFS: list[tuple[str, dict[str, Any], str]] = [
    # Basic fields
    ("basic", {"name": int(BasicProperty.CODE)}, "CODE"),
    ("basic", {"name": int(BasicProperty.NAME)}, "NAME"),
    # Simple properties (price & valuation)
    ("simple", {"name": int(SimpleProperty.PRICE)}, "PRICE"),
    ("simple", {"name": int(SimpleProperty.MARKET_CAP)}, "MARKET_CAP"),
    ("simple", {"name": int(SimpleProperty.PE_TTM)}, "PE_TTM"),
    ("simple", {"name": int(SimpleProperty.PB)}, "PB"),
    ("simple", {"name": int(SimpleProperty.VOLUME_RATIO)}, "VOLUME_RATIO"),
    # Cumulative properties (price changes over periods)
    ("cumulative", {"name": int(CumulativeProperty.PRICE_CHANGE_PCT), "days": 7}, "PRICE_CHANGE_1W"),
    ("cumulative", {"name": int(CumulativeProperty.PRICE_CHANGE_PCT), "days": 30}, "PRICE_CHANGE_1M"),
    ("cumulative", {"name": int(CumulativeProperty.PRICE_CHANGE_PCT), "days": 90}, "PRICE_CHANGE_3M"),
    ("cumulative", {"name": int(CumulativeProperty.TURNOVER_RATIO), "days": 1}, "TURNOVER_RATE"),
    # Financial properties
    ("financial", {"name": int(FinancialProperty.ROE), "term": int(Term.LATEST)}, "ROE"),
    ("financial", {"name": int(FinancialProperty.ROA_TTM), "term": int(Term.LATEST)}, "ROA"),
    ("financial", {"name": int(FinancialProperty.GROSS_PROFIT_RATIO), "term": int(Term.LATEST)}, "GROSS_PROFIT_RATIO"),
    ("financial", {"name": int(FinancialProperty.PS_TTM), "term": int(Term.LATEST)}, "PS_TTM"),
    # Technical indicators (daily)
    ("indicator", {"name": int(Indicator.MA5), "period": int(Period.DAY)}, "MA_5"),
    ("indicator", {"name": int(Indicator.MA20), "period": int(Period.DAY)}, "MA_20"),
    ("indicator", {"name": int(Indicator.MA60), "period": int(Period.DAY)}, "MA_60"),
    ("indicator", {"name": int(Indicator.RSI_12), "period": int(Period.DAY)}, "RSI_14"),
    ("indicator", {"name": int(Indicator.KDJ_K), "period": int(Period.DAY)}, "KDJ_K"),
    ("indicator", {"name": int(Indicator.KDJ_D), "period": int(Period.DAY)}, "KDJ_D"),
    ("indicator", {"name": int(Indicator.KDJ_J), "period": int(Period.DAY)}, "KDJ_J"),
    ("indicator", {"name": int(Indicator.BOLL_UPPER), "period": int(Period.DAY)}, "BOLL_UPPER"),
    ("indicator", {"name": int(Indicator.BOLL_MIDDLE), "period": int(Period.DAY)}, "BOLL_MID"),
    ("indicator", {"name": int(Indicator.BOLL_LOWER), "period": int(Period.DAY)}, "BOLL_LOWER"),
    # Capital flow (featured) – 1-day
    ("featured", {"name": int(FeaturedProperty.CASH_FLOW_TOTAL_NET_IN), "period": int(CashFlowPeriod.DAY)}, "CAPITAL_FLOW_1D"),
    ("featured", {"name": int(FeaturedProperty.CASH_FLOW_MAIN_NET_IN), "period": int(CashFlowPeriod.DAY)}, "CAPITAL_FLOW_MAIN_1D"),
    # Capital flow (featured) – 5-day (weekly)
    ("featured", {"name": int(FeaturedProperty.CASH_FLOW_TOTAL_NET_IN), "period": int(CashFlowPeriod.WEEK)}, "CAPITAL_FLOW_5D"),
    ("featured", {"name": int(FeaturedProperty.CASH_FLOW_MAIN_NET_IN), "period": int(CashFlowPeriod.WEEK)}, "CAPITAL_FLOW_MAIN_5D"),
]

# Map builder method to the corresponding add_retrieve_* callable
_BUILDER_ATTR: dict[str, str] = {
    "basic": "add_retrieve_basic",
    "simple": "add_retrieve_simple",
    "cumulative": "add_retrieve_cumulative",
    "financial": "add_retrieve_financial",
    "indicator": "add_retrieve_indicator",
    "featured": "add_retrieve_featured",
}


def _build_retrieve_key(prop_type: str, params: dict[str, Any]) -> str:
    """Build a unique key from property type + params for column mapping."""
    name = params["name"]
    extras = {k: v for k, v in params.items() if k != "name"}
    if not extras:
        return f"{prop_type}:{name}"
    extra_str = ",".join(f"{k}={v}" for k, v in sorted(extras.items()))
    return f"{prop_type}:{name}({extra_str})"


class StockScreenCollector(FutuCollector):
    """Retrieves multi-factor data via get_stock_screen (protocol 3252)."""

    def __init__(self, market: str = "us", page_size: int = 200):
        super().__init__(market=market, rate_limit_per_min=10)
        self._scr_market = ScrMarket.US if market == "us" else ScrMarket.HK
        self._page_size = min(page_size, 200)  # API max is ~200 per page
        # Column-name → retrieve-key mapping built from _RETRIEVE_DEFS
        self._col_to_key: dict[str, str] = {}
        for method, params, col in _RETRIEVE_DEFS:
            self._col_to_key[col] = _build_retrieve_key(method, params)

    def _build_request(self, page_from: int) -> StockScreenRequest:
        """Construct a StockScreenRequest for one page."""
        req = StockScreenRequest()
        req.page_from = page_from
        req.page_count = self._page_size
        # Market filter
        req.add_simple_field(field=int(SimpleField.MARKET), values=[int(self._scr_market)])
        # Add all retrieve fields
        for method, params, _col in _RETRIEVE_DEFS:
            attr = _BUILDER_ATTR[method]
            getattr(req, attr)(**params)
        return req

    @staticmethod
    def _parse_item(item: dict, col_to_key: dict[str, str]) -> dict[str, Any]:
        """Parse one StockScreen item dict into a flat row."""
        row: dict[str, Any] = {"stock_id": item.get("stock_id")}
        # Build reverse map: retrieve-key → value
        key_to_val: dict[str, Any] = {}
        for result in item.get("results", []):
            prop_type = result.get("type", "")
            prop_info = result.get("property", {})
            params = dict(prop_info)
            name = params.pop("name", None)
            if name is None:
                continue
            params.setdefault("name", name)
            key = _build_retrieve_key(prop_type, params)
            vt = result.get("value_type")
            if vt == 1:  # string
                key_to_val[key] = result.get("sval")
            elif vt == 4:  # double
                key_to_val[key] = result.get("dval")
            elif vt == 2:  # int64
                key_to_val[key] = result.get("ival")
            elif vt == 3:  # int64 array
                key_to_val[key] = result.get("aval")
            else:
                key_to_val[key] = None
        # Map to column names
        for col, key in col_to_key.items():
            row[col] = key_to_val.get(key)
        return row

    def collect_all(self) -> pd.DataFrame:
        """Fetch all pages via paginated stock_screen API."""
        all_data: list[dict[str, Any]] = []
        page_from = 0
        while True:
            self._rate_limit()
            req = self._build_request(page_from)
            ret, data = self._get_ctx().get_stock_screen(req)
            if ret != RET_OK:
                logger.error("get_stock_screen failed: ret=%s data=%s", ret, data)
                break
            last_page: bool
            all_count: int
            items: list[dict]
            last_page, all_count, items = data
            if items:
                for item in items:
                    all_data.append(self._parse_item(item, self._col_to_key))
                logger.info("Page %d: got %d items (total=%d)", page_from, len(items), all_count)
            if last_page or not items:
                break
            page_from += self._page_size
        self._close()
        if not all_data:
            logger.warning("No data returned for market=%s", self.market)
            return pd.DataFrame()
        result = pd.DataFrame(all_data)
        logger.info("StockScreen[%s]: %d stocks, %d columns", self.market.upper(), len(result), len(result.columns))
        return result
