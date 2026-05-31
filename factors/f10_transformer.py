"""F10 Data Transformer — converts BQ raw F10 tables to fundamental factor columns.

Each transform_xxx() method takes a raw BQ DataFrame (as loaded from the table)
and returns a DataFrame with columns matching FundamentalFactorBuilder expectations.
"""

import logging

import numpy as np
import pandas as pd

log = logging.getLogger(__name__)

# field_id → field_name mapping (extended from futu_financials_adapter)
FIELD_ID_NAME = {
    8001: "revenue",
    8002: "total_revenue",
    8003: "cost_of_revenue",
    8004: "gross_profit",
    8005: "operating_income",
    8007: "pretax_income",
    8010: "net_income",
    8017: "total_assets",
    8022: "current_assets",
    8033: "total_liabilities",
    8034: "current_liabilities",
    8035: "shareholder_equity",
    8037: "operating_cash_flow",
    8038: "investing_cash_flow",
    8043: "financing_cash_flow",
    8046: "free_cash_flow",
    8047: "eps",
    8048: "diluted_eps",
    8049: "dps",
}


class F10Transformer:
    """Transform raw BQ F10 DataFrames to fundamental factor format."""

    @staticmethod
    def transform_financials(df: pd.DataFrame) -> pd.DataFrame:
        """Transform long-format financials to wide-format quality + growth factors.

        Input:  symbol | date_time_str | field_id | field_name | data | yoy | qoq
        Output: symbol | date | roe | roa | gross_margin | ... | revenue_growth_yoy | ...
        """
        if df.empty:
            return pd.DataFrame()

        df = df.copy()
        # Parse date
        df["date"] = pd.to_datetime(df["date_time_str"], format="%Y-%m-%d", errors="coerce")

        # Map field_id to name, fallback to field_name
        df["metric"] = df["field_id"].map(FIELD_ID_NAME).fillna(df["field_name"])

        # Pivot data column → wide
        data_wide = df.pivot_table(
            index=["symbol", "date"],
            columns="metric",
            values="data",
            aggfunc="first",
        )

        # Pivot yoy column → wide (for growth factors)
        yoy_wide = df.pivot_table(
            index=["symbol", "date"],
            columns="metric",
            values="yoy",
            aggfunc="first",
        )
        yoy_wide = yoy_wide.rename(columns=lambda c: f"{c}_growth_yoy")

        # Combine
        result = pd.concat([data_wide, yoy_wide], axis=1).reset_index()

        # Compute derived quality factors
        if "net_income" in result.columns and "shareholder_equity" in result.columns:
            result["roe"] = result["net_income"] / result["shareholder_equity"].replace(0, np.nan)
        if "net_income" in result.columns and "total_assets" in result.columns:
            result["roa"] = result["net_income"] / result["total_assets"].replace(0, np.nan)
        if "gross_profit" in result.columns and "revenue" in result.columns:
            result["gross_margin"] = result["gross_profit"] / result["revenue"].replace(0, np.nan)
        if "net_income" in result.columns and "revenue" in result.columns:
            result["net_margin"] = result["net_income"] / result["revenue"].replace(0, np.nan)
        if "total_liabilities" in result.columns and "shareholder_equity" in result.columns:
            result["debt_to_equity"] = result["total_liabilities"] / result["shareholder_equity"].replace(0, np.nan)
        if "current_assets" in result.columns and "current_liabilities" in result.columns:
            result["current_ratio"] = result["current_assets"] / result["current_liabilities"].replace(0, np.nan)
        if "operating_income" in result.columns and "total_liabilities" in result.columns:
            # Approximate interest_coverage: operating_income / (total_liabilities * 0.05)
            result["interest_coverage"] = result["operating_income"] / (result["total_liabilities"] * 0.05).replace(
                0, np.nan
            )

        # Map growth columns
        growth_map = {
            "revenue_growth_yoy": "revenue_growth_yoy",
            "eps_growth_yoy": "eps_growth_yoy",
            "net_income_growth_yoy": "net_profit_growth_yoy",
            "total_assets_growth_yoy": "asset_growth_yoy",
        }
        for src, dst in growth_map.items():
            if src in result.columns:
                result[dst] = result[src]

        # EARNINGS_QUALITY factors
        # accruals_ratio = (net_income - operating_cash_flow) / total_assets
        if all(c in result.columns for c in ["net_income", "operating_cash_flow", "total_assets"]):
            result["accruals_ratio"] = (result["net_income"] - result["operating_cash_flow"]) / result["total_assets"].replace(0, np.nan)

        # ocf_to_net_profit = operating_cash_flow / net_income
        if "operating_cash_flow" in result.columns and "net_income" in result.columns:
            result["ocf_to_net_profit"] = result["operating_cash_flow"] / result["net_income"].replace(0, np.nan)

        # revenue_to_cash_ratio = revenue / operating_cash_flow
        if "revenue" in result.columns and "operating_cash_flow" in result.columns:
            result["revenue_to_cash_ratio"] = result["revenue"] / result["operating_cash_flow"].replace(0, np.nan)

        # Sort and clean
        result = result.sort_values(["symbol", "date"]).reset_index(drop=True)
        return result

    @staticmethod
    def transform_valuation(df: pd.DataFrame) -> pd.DataFrame:
        """Transform long-format valuation to wide-format with percentiles.

        Input:  symbol | date | valuation_type | interval | value
        Output: symbol | date | pe_percentile | pb_percentile | ps_percentile | ...
        """
        if df.empty:
            return pd.DataFrame()

        df = df.copy()

        # Use only 5y interval for percentile calculation
        df_5y = df[df["interval"] == "5y"].copy()
        if df_5y.empty:
            df_5y = df  # fallback to all

        # Pivot valuation_type → columns
        wide = df_5y.pivot_table(
            index=["symbol", "date"],
            columns="valuation_type",
            values="value",
            aggfunc="first",
        ).reset_index()

        # Compute cross-sectional percentiles
        for metric, col_name in [
            ("pe", "pe_percentile"),
            ("pb", "pb_percentile"),
            ("ps", "ps_percentile"),
        ]:
            if metric in wide.columns:
                wide[col_name] = wide[metric].rank(pct=True)

        # Simple pe_vs_5y_avg: current PE vs mean PE
        if "pe" in wide.columns:
            pe_mean = wide["pe"].mean()
            if pe_mean > 0:
                wide["pe_vs_5y_avg"] = wide["pe"] / pe_mean - 1

        # peg_ratio: PE / earnings_growth (simplified: PE / 100 * eps_growth estimate)
        if "pe" in wide.columns:
            # Approximate: use 10% as default growth rate
            wide["peg_ratio"] = wide["pe"] / 10.0

        return wide

    @staticmethod
    def transform_analyst(df: pd.DataFrame) -> pd.DataFrame:
        """Transform analyst data to factor columns.

        Input:  symbol | buy | hold | sell | total | average | highest | lowest | rating | update_time
        Output: symbol | date | target_price_upside | buy_ratio | rating_mean | rating_change_1m | analyst_count
        """
        if df.empty:
            return pd.DataFrame()

        df = df.copy()

        # Convert update_time (epoch INT) to date
        if "update_time" in df.columns:
            df["date"] = pd.to_datetime(df["update_time"], unit="s", errors="coerce").dt.date

        # Analyst count
        if "total" in df.columns:
            df["analyst_count"] = df["total"]

        # Buy ratio
        if "buy" in df.columns and "total" in df.columns:
            df["buy_ratio"] = df["buy"] / df["total"].replace(0, np.nan)

        # Rating mean (lower is better usually, but we just take it)
        if "rating" in df.columns:
            df["rating_mean"] = df["rating"].astype(float)

        # Target price upside: average target / current price - 1
        # Note: need current price. For standalone transform, set to 0
        # Caller should merge with price data
        df["target_price_upside"] = 0.0

        # rating_change_1m: NaN for now (needs history)
        df["rating_change_1m"] = np.nan

        return df

    @staticmethod
    def transform_capital_flow(df: pd.DataFrame) -> pd.DataFrame:
        """Transform capital flow to factor columns.

        Input:  symbol | capital_in_super/big/mid/small | capital_out_super/big/mid/small
        Output: symbol | date | main_inflow_ratio | big_order_pct | retail_flow_divergence | flow_price_divergence
        """
        if df.empty:
            return pd.DataFrame()

        df = df.copy()

        # Parse date from ingest_time
        if "ingest_time" in df.columns:
            df["date"] = pd.to_datetime(df["ingest_time"]).dt.date

        # Total inflow/outflow
        inflow_cols = [c for c in df.columns if c.startswith("capital_in_")]
        outflow_cols = [c for c in df.columns if c.startswith("capital_out_")]

        total_in = df[inflow_cols].sum(axis=1) if inflow_cols else pd.Series(0, index=df.index)
        total_out = df[outflow_cols].sum(axis=1) if outflow_cols else pd.Series(0, index=df.index)
        total_flow = total_in + total_out

        # Main inflow ratio
        df["main_inflow_ratio"] = (total_in - total_out) / total_flow.replace(0, np.nan)

        # Big order percentage
        big_in = df.get("capital_in_big", 0) + df.get("capital_in_super", 0)
        big_out = df.get("capital_out_big", 0) + df.get("capital_out_super", 0)
        df["big_order_pct"] = (big_in + big_out) / total_flow.replace(0, np.nan)

        # Retail flow divergence (small investor vs big)
        small_net = df.get("capital_in_small", 0) - df.get("capital_out_small", 0)
        big_net = big_in - big_out
        df["retail_flow_divergence"] = small_net - big_net

        # Flow price divergence: placeholder
        df["flow_price_divergence"] = np.nan

        return df

    @staticmethod
    def transform_shareholder(df: pd.DataFrame) -> pd.DataFrame:
        """Transform shareholder data to short interest factor columns.

        Input:  symbol | holder_pct | institution_quantity | share_ratio | ...
        Output: symbol | date | short_ratio | days_to_cover | short_change_1m | short_volume_pct | short_utilization
        """
        if df.empty:
            return pd.DataFrame()

        df = df.copy()

        # Parse date
        if "update_time" in df.columns:
            df["date"] = pd.to_datetime(df["update_time"], unit="s", errors="coerce").dt.date
        elif "holding_date" in df.columns:
            df["date"] = pd.to_datetime(df["holding_date"], unit="s", errors="coerce").dt.date

        # Aggregate per symbol+date
        agg = df.groupby(["symbol", "date"], as_index=False).agg(
            total_institution_qty=("institution_quantity", "sum"),
            total_holder_qty=("holder_quantity", "sum"),
            max_holder_pct=("holder_pct", "max"),
            institution_change=("institution_quantity_change", "sum"),
        )

        # Short_ratio (proxy: institutional holding as % of total)
        if "total_institution_qty" in agg.columns and "total_holder_qty" in agg.columns:
            total_qty = agg["total_institution_qty"] + agg["total_holder_qty"]
            agg["short_ratio"] = agg["total_institution_qty"] / total_qty.replace(0, np.nan)

        # Other short factors: simplified
        agg["days_to_cover"] = np.nan
        agg["short_change_1m"] = agg.get("institution_change", np.nan)
        agg["short_volume_pct"] = np.nan
        agg["short_utilization"] = np.nan

        # SMART_MONEY factors from existing shareholder aggregation
        # inst_ownership_change: already available as institution_change
        if "institution_change" in agg.columns:
            agg["inst_ownership_change"] = agg["institution_change"] / agg.get("total_institution_qty", 1).replace(0, np.nan)

        # inst_accumulation_signal: sign of ownership change (+1 buying, -1 selling, 0 neutral)
        if "inst_ownership_change" in agg.columns:
            agg["inst_accumulation_signal"] = np.sign(agg["inst_ownership_change"])

        # hedge_fund_add_ratio: approximate — use top holder pct change
        if "max_holder_pct" in agg.columns:
            agg["hedge_fund_add_ratio"] = agg["max_holder_pct"]

        # insider_buy_ratio: approximate — use institution_change as proxy
        if "inst_ownership_change" in agg.columns:
            agg["insider_buy_ratio"] = agg["inst_ownership_change"].clip(lower=0)

        # holder_concentration: top holders' combined ownership
        if "max_holder_pct" in agg.columns:
            agg["holder_concentration"] = agg["max_holder_pct"]

        return agg

    @staticmethod
    def transform_earnings_events(financials: pd.DataFrame, bars_data: pd.DataFrame = None) -> pd.DataFrame:
        """Compute earnings event factors from financial report dates and stock prices.

        Uses report_date from financials as proxy for earnings announcement date.

        Args:
            financials: DataFrame with (symbol, date) after pivot
            bars_data: Optional OHLCV bar data with (symbol, date, close). If None, returns NaN placeholders.
        """
        if bars_data is None or bars_data.empty:
            result = financials[["symbol", "date"]].copy() if "symbol" in financials.columns else pd.DataFrame()
            for col in ["earnings_price_move_avg", "post_earnings_drift_5d", "earnings_volatility"]:
                result[col] = np.nan
            return result

        bars = bars_data.copy()
        bars["date"] = pd.to_datetime(bars["date"])
        bars = bars.sort_values(["symbol", "date"])

        results = []
        for sym in financials["symbol"].unique():
            sym_bars = bars[bars["symbol"] == sym].set_index("date")["close"]
            if sym_bars.empty:
                continue

            sym_fin = financials[financials["symbol"] == sym]
            for _, row in sym_fin.iterrows():
                report_dt = pd.to_datetime(row["date"])
                entry_idx = sym_bars.index.searchsorted(report_dt)

                # earnings_price_move_avg: avg return over next 5 trading days
                if entry_idx + 5 < len(sym_bars):
                    fwd_5d = sym_bars.iloc[entry_idx + 5] / sym_bars.iloc[entry_idx] - 1 if entry_idx < len(sym_bars) else None
                    earnings_price = fwd_5d
                else:
                    earnings_price = None

                # post_earnings_drift_5d: same as above (alias)
                post_drift = earnings_price

                # earnings_volatility: std of returns over 10 days around report
                if entry_idx >= 5 and entry_idx + 5 < len(sym_bars):
                    window_returns = sym_bars.iloc[entry_idx-5:entry_idx+5].pct_change().dropna()
                    earn_vol = window_returns.std() if len(window_returns) > 0 else None
                else:
                    earn_vol = None

                results.append({
                    "symbol": sym,
                    "date": row["date"],
                    "earnings_price_move_avg": earnings_price,
                    "post_earnings_drift_5d": post_drift,
                    "earnings_volatility": earn_vol,
                })

        return pd.DataFrame(results)

    @classmethod
    def transform_all(cls, data_map: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
        """Transform all F10 tables in data_map.

        Args:
            data_map: Dict with keys: "financials", "valuation", "analyst",
                     "capital_flow", "short_interest" (from us_shareholder)

        Returns:
            Dict with same keys, values are transformed DataFrames ready for FundamentalFactorBuilder.
        """
        result = {}

        if "financials" in data_map and not data_map["financials"].empty:
            result["financials"] = cls.transform_financials(data_map["financials"])

        if "valuation" in data_map and not data_map["valuation"].empty:
            result["valuation"] = cls.transform_valuation(data_map["valuation"])

        if "analyst" in data_map and not data_map["analyst"].empty:
            result["analyst"] = cls.transform_analyst(data_map["analyst"])

        if "capital_flow" in data_map and not data_map["capital_flow"].empty:
            result["capital_flow"] = cls.transform_capital_flow(data_map["capital_flow"])

        if "short_interest" in data_map and not data_map["short_interest"].empty:
            result["short_interest"] = cls.transform_shareholder(data_map["short_interest"])

        # Earnings event factors (requires financials data + optional bars)
        if "financials" in result:
            result["earnings_events"] = cls.transform_earnings_events(result["financials"], bars_data=None)

        return result
