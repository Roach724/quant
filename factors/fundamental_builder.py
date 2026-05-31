"""FundamentalFactorBuilder — F10 fundamental + sentiment + flow factors (~41 factors).

Computes factors from BigQuery F10 raw data. Follows the same interface patterns
as TechFactorBuilder: compute → process_factors → build_dataset.
"""
from __future__ import annotations

import logging
from typing import Callable

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


class FundamentalFactorBuilder:
    """Compute 41 F10 fundamental, sentiment, capital flow, and event-driven factors."""

    QUALITY_COLS = ["roe", "roa", "gross_margin", "net_margin", "debt_to_equity",
                    "current_ratio", "interest_coverage"]
    GROWTH_COLS = ["revenue_growth_yoy", "eps_growth_yoy", "net_profit_growth_yoy", "asset_growth_yoy"]
    EARNINGS_QUALITY_COLS = ["accruals_ratio", "ocf_to_net_profit", "revenue_to_cash_ratio"]
    VALUATION_COLS = ["pe_percentile", "pb_percentile", "ps_percentile", "pe_vs_5y_avg", "peg_ratio"]
    SHORT_COLS = ["short_ratio", "days_to_cover", "short_change_1m", "short_volume_pct", "short_utilization"]
    FLOW_COLS = ["main_inflow_ratio", "big_order_pct", "retail_flow_divergence", "flow_price_divergence"]
    ANALYST_COLS = ["target_price_upside", "buy_ratio", "rating_mean", "rating_change_1m", "analyst_count"]
    SMART_MONEY_COLS = ["inst_ownership_change", "inst_accumulation_signal",
                        "hedge_fund_add_ratio", "insider_buy_ratio", "holder_concentration"]
    EARNINGS_EVENT_COLS = ["earnings_price_move_avg", "post_earnings_drift_5d", "earnings_volatility"]

    LABEL_COLS = ["fwd_ret_5d", "fwd_ret_20d"]

    ALL_FACTOR_COLS = (
        QUALITY_COLS + GROWTH_COLS + EARNINGS_QUALITY_COLS
        + VALUATION_COLS + SHORT_COLS + FLOW_COLS + ANALYST_COLS
        + SMART_MONEY_COLS + EARNINGS_EVENT_COLS
    )

    def __init__(self):
        self.factor_names: list[str] = []

    @staticmethod
    def _quality_factors(financials: pd.DataFrame) -> pd.DataFrame:
        df = pd.DataFrame(index=financials.index)
        for col in FundamentalFactorBuilder.QUALITY_COLS:
            if col in financials.columns:
                df[col] = financials[col]
        return df

    @staticmethod
    def _growth_factors(financials: pd.DataFrame) -> pd.DataFrame:
        df = pd.DataFrame(index=financials.index)
        map_ = {"revenue_growth_yoy": "revenue", "eps_growth_yoy": "eps",
                "net_profit_growth_yoy": "net_profit", "asset_growth_yoy": "total_assets"}
        for factor_col, raw_col in map_.items():
            if raw_col in financials.columns:
                df[factor_col] = financials[raw_col].pct_change(4)
        return df

    @staticmethod
    def _valuation_factors(valuation: pd.DataFrame) -> pd.DataFrame:
        df = pd.DataFrame(index=valuation.index)
        for col in FundamentalFactorBuilder.VALUATION_COLS:
            if col in valuation.columns:
                df[col] = valuation[col]
        return df

    @staticmethod
    def _short_factors(short_interest: pd.DataFrame) -> pd.DataFrame:
        df = pd.DataFrame(index=short_interest.index)
        for col in FundamentalFactorBuilder.SHORT_COLS:
            if col in short_interest.columns:
                df[col] = short_interest[col]
        return df

    @staticmethod
    def _capital_flow_factors(flow: pd.DataFrame) -> pd.DataFrame:
        df = pd.DataFrame(index=flow.index)
        for col in FundamentalFactorBuilder.FLOW_COLS:
            if col in flow.columns:
                df[col] = flow[col]
        return df

    @staticmethod
    def _analyst_factors(analyst: pd.DataFrame) -> pd.DataFrame:
        df = pd.DataFrame(index=analyst.index)
        for col in FundamentalFactorBuilder.ANALYST_COLS:
            if col in analyst.columns:
                df[col] = analyst[col]
        return df

    @staticmethod
    def _earnings_quality_factors(financials: pd.DataFrame) -> pd.DataFrame:
        df = pd.DataFrame(index=financials.index)
        for col in FundamentalFactorBuilder.EARNINGS_QUALITY_COLS:
            if col in financials.columns:
                df[col] = financials[col]
        return df

    @staticmethod
    def _smart_money_factors(shareholder: pd.DataFrame) -> pd.DataFrame:
        df = pd.DataFrame(index=shareholder.index)
        for col in FundamentalFactorBuilder.SMART_MONEY_COLS:
            if col in shareholder.columns:
                df[col] = shareholder[col]
        return df

    @staticmethod
    def _earnings_event_factors(earnings: pd.DataFrame) -> pd.DataFrame:
        df = pd.DataFrame(index=earnings.index)
        for col in FundamentalFactorBuilder.EARNINGS_EVENT_COLS:
            if col in earnings.columns:
                df[col] = earnings[col]
        return df

    def compute(self, factor_names: list[str], data_map: dict[str, pd.DataFrame]) -> pd.DataFrame:
        result = pd.DataFrame()

        fin = data_map.get("financials", pd.DataFrame())
        if not fin.empty:
            result = pd.concat([result, self._quality_factors(fin), self._growth_factors(fin)], axis=1)

        val = data_map.get("valuation", pd.DataFrame())
        if not val.empty:
            result = pd.concat([result, self._valuation_factors(val)], axis=1)

        si = data_map.get("short_interest", pd.DataFrame())
        if not si.empty:
            result = pd.concat([result, self._short_factors(si)], axis=1)

        cf = data_map.get("capital_flow", pd.DataFrame())
        if not cf.empty:
            result = pd.concat([result, self._capital_flow_factors(cf)], axis=1)

        an = data_map.get("analyst", pd.DataFrame())
        if not an.empty:
            result = pd.concat([result, self._analyst_factors(an)], axis=1)

        # Earnings quality from financials
        fin2 = data_map.get("financials", pd.DataFrame())
        if not fin2.empty:
            result = pd.concat([result, self._earnings_quality_factors(fin2)], axis=1)

        # Smart money from shareholder data
        si2 = data_map.get("short_interest", pd.DataFrame())
        if not si2.empty:
            result = pd.concat([result, self._smart_money_factors(si2)], axis=1)

        # Earnings event data
        earn = data_map.get("earnings_events", pd.DataFrame())
        if not earn.empty:
            result = pd.concat([result, self._earnings_event_factors(earn)], axis=1)

        available = [c for c in factor_names if c in result.columns]
        self.factor_names = available
        return result[available] if available else pd.DataFrame()

    def process_factors(self, factor_df: pd.DataFrame, winsor_pct: float = 0.01) -> pd.DataFrame:
        df = factor_df.copy()
        factor_cols = [c for c in df.columns
                       if c not in ("fwd_ret_5d", "fwd_ret_20d", "symbol", "date")]
        for col in factor_cols:
            lo = df[col].quantile(winsor_pct)
            hi = df[col].quantile(1 - winsor_pct)
            df[col] = df[col].clip(lo, hi)
        for col in factor_cols:
            mean = df[col].mean()
            std = df[col].std()
            if std is not None and not pd.isna(std) and std > 1e-8:
                df[col] = (df[col] - mean) / std
        df[factor_cols] = df[factor_cols].fillna(0)
        return df

    def build_dataset(
        self, symbols: list[str], start: str, end: str,
        data_loader: Callable[[str, str, str], dict[str, pd.DataFrame]],
    ) -> pd.DataFrame:
        all_factors: list[pd.DataFrame] = []
        for i, sym in enumerate(symbols):
            try:
                data_map = data_loader(sym, start, end)
                if not data_map:
                    continue
                factors = self.compute(self.ALL_FACTOR_COLS, data_map)
                if factors.empty:
                    continue
                factors["symbol"] = sym
                all_factors.append(factors)
                if (i + 1) % 10 == 0:
                    logger.info("  F10 factors: %d/%d stocks", i + 1, len(symbols))
            except Exception:
                logger.debug("  %s: F10 factor build failed", sym, exc_info=True)
        if not all_factors:
            return pd.DataFrame()
        return pd.concat(all_factors, ignore_index=True)
