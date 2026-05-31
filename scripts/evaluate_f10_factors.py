#!/usr/bin/env python3.12
"""Evaluate F10 factor IC from BQ raw tables.

Loads F10 raw data → computes factors via FundamentalFactorBuilder →
merges with forward returns → calculates IC/t-stat/coverage →
registers passing factors to BQ factor_registry.

Usage:
    python3.12 scripts/evaluate_f10_factors.py --start 2020-01-01 --end 2026-05-30
    python3.12 scripts/evaluate_f10_factors.py --register   # also register passing factors
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import argparse
import json
import logging

import numpy as np
import pandas as pd
from google.cloud import bigquery
from scipy.stats import spearmanr

from factors.fundamental_builder import FundamentalFactorBuilder
from factors.f10_transformer import F10Transformer
from factors.registry import FactorRegistry

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("f10_ic")

PROJECT = "deductive-notch-495015-c2"
DATASET = f"{PROJECT}.quant"

# BQ table → data_map key expected by FundamentalFactorBuilder.compute()
# NOTE: us_shareholder maps to "short_interest" because SHORT_COLS
# (short_ratio, days_to_cover, etc.) come from shareholder data.
TABLE_TO_KEY = {
    "us_valuation": "valuation",
    "us_financials": "financials",
    "us_analyst": "analyst",
    "us_capital_flow": "capital_flow",
    "us_shareholder": "short_interest",
}

# F10 tables to evaluate
F10_TABLES = list(TABLE_TO_KEY.keys())

# Tables with JSON data column (as opposed to direct columnar schema)
JSON_SOURCE_TABLES = {"us_analyst", "us_capital_flow", "us_shareholder"}

# Timestamp column per table: (col_name, bq_date_expr_template or None for DATE(col))
TS_COL = {
    "us_valuation": ("ingest_time", None),
    "us_financials": ("date_time_str", "PARSE_TIMESTAMP('%Y-%m-%d', {col})"),
    "us_analyst": ("update_time", "TIMESTAMP_SECONDS(CAST({col} AS INT64))"),
    "us_capital_flow": ("ingest_time", None),
    "us_shareholder": ("update_time", "TIMESTAMP_SECONDS(CAST({col} AS INT64))"),
}


# ── Category mapping (from FundamentalFactorBuilder) ──
def _build_category_map(ffb):
    """Build factor_name → category dict."""
    m = {}
    for name in ffb.QUALITY_COLS:
        m[name] = "quality"
    for name in ffb.GROWTH_COLS:
        m[name] = "growth"
    for name in ffb.EARNINGS_QUALITY_COLS:
        m[name] = "earnings_quality"
    for name in ffb.VALUATION_COLS:
        m[name] = "valuation"
    for name in ffb.SHORT_COLS:
        m[name] = "short_sentiment"
    for name in ffb.FLOW_COLS:
        m[name] = "capital_flow"
    for name in ffb.ANALYST_COLS:
        m[name] = "analyst"
    for name in ffb.SMART_MONEY_COLS:
        m[name] = "smart_money"
    for name in ffb.EARNINGS_EVENT_COLS:
        m[name] = "earnings_event"
    return m


def load_f10_table(table: str, start: str, end: str) -> pd.DataFrame | None:
    """Load a single F10 table from BQ, returning None on failure."""
    client = bigquery.Client(project=PROJECT)
    ts_entry = TS_COL.get(table, ("ingest_time", None))
    col_name, date_expr_template = ts_entry
    if date_expr_template:
        ts_expr = date_expr_template.format(col=col_name)
    else:
        ts_expr = col_name
    date_filter = f"DATE({ts_expr}) BETWEEN '{start}' AND '{end}'"
    query = f"""
        SELECT *
        FROM `{DATASET}.{table}`
        WHERE {date_filter}
    """
    log.info("Loading %s ...", table)
    try:
        df = client.query(query).to_dataframe()
    except Exception as e:
        log.warning("  %s: query failed — %s", table, e)
        return None

    if df.empty:
        log.info("  %s: 0 rows (empty date range)", table)
        return None

    log.info("  %s: %d rows", table, len(df))
    return df


def expand_json_data(df: pd.DataFrame) -> pd.DataFrame:
    """Parse the 'data' JSON column into individual factor columns.

    The raw F10 tables (financials, analyst, capital_flow, shareholder,
    short_interest) store values as a JSON string in the 'data' column.
    Each row's JSON keys become columns in the returned DataFrame.

    Returns a DataFrame with the same index, plus expanded columns.
    """
    if "data" not in df.columns:
        log.debug("  No 'data' column; skipping JSON expansion")
        return df

    parsed = df["data"].apply(_safe_json_parse)
    expanded = pd.DataFrame(parsed.tolist(), index=df.index)

    # Merge with original metadata columns (drop raw 'data')
    meta_cols = [c for c in df.columns if c != "data"]
    result = pd.concat([df[meta_cols], expanded], axis=1)
    return result


def _safe_json_parse(val):
    """Parse a JSON string safely, returning empty dict on failure."""
    if isinstance(val, dict):
        return val
    if isinstance(val, str):
        try:
            return json.loads(val)
        except (json.JSONDecodeError, TypeError):
            return {}
    return {}


def pivot_valuation(df: pd.DataFrame) -> pd.DataFrame:
    """Pivot long-format valuation table to wide format.

    Input:  symbol | valuation_type | date | value | ...
    Output: symbol | date | pe_percentile | pb_percentile | ...
    """
    if "valuation_type" not in df.columns or "value" not in df.columns:
        log.warning("  valuation table missing valuation_type/value columns; using raw")
        return df

    # Keep only essential columns
    keep = ["symbol", "date", "valuation_type", "value"]
    sub = df[[c for c in keep if c in df.columns]].copy()

    # Drop rows with missing symbol or date
    sub = sub.dropna(subset=["symbol", "date"])

    # Convert date to date-only
    if "date" in sub.columns:
        sub["date"] = pd.to_datetime(sub["date"], errors="coerce").dt.date

    # Pivot: valuation_type → columns, value → values
    pivoted = sub.pivot_table(
        index=["symbol", "date"],
        columns="valuation_type",
        values="value",
        aggfunc="first",
    ).reset_index()

    # Flatten MultiIndex columns if any
    if isinstance(pivoted.columns, pd.MultiIndex):
        pivoted.columns = ["_".join(c).strip("_") for c in pivoted.columns.values]

    return pivoted


def preprocess_table(table_name: str, df: pd.DataFrame) -> pd.DataFrame:
    """Preprocess a raw F10 table into factor-ready format.

    Steps:
      1. If JSON-source: parse data column
      2. If valuation: pivot wide
      3. Normalize symbol/date columns
      4. Drop duplicate rows per (symbol, date)

    Returns DataFrame with symbol + date + factor columns.
    """
    if table_name in JSON_SOURCE_TABLES:
        df = expand_json_data(df)

    if table_name == "us_valuation" and False:  # handled by F10Transformer
        df = pivot_valuation(df)

    # Ensure symbol column exists
    if "symbol" not in df.columns:
        log.warning("  %s: no 'symbol' column; cannot align", table_name)
        return pd.DataFrame()

    # Normalize symbol: strip "US." prefix if present
    df["symbol"] = df["symbol"].astype(str).str.replace("US.", "", regex=False).str.replace("US_", "", regex=False)

    # Ensure date column exists; look for common date columns
    date_col = None
    for candidate in ("date", "trade_date", "report_date", "date_time_str"):
        if candidate in df.columns:
            date_col = candidate
            break

    if date_col is None:
        # Fallback: try to derive from ingest_time
        for candidate in ("ingest_time", "update_time", "fetched_at"):
            if candidate in df.columns:
                df["date"] = pd.to_datetime(df[candidate], errors="coerce").dt.date
                date_col = "date"
                break

    if date_col is None:
        log.warning("  %s: no date column found; cannot align temporally", table_name)
        return pd.DataFrame()

    if date_col != "date":
        df["date"] = pd.to_datetime(df[date_col], errors="coerce").dt.date

    # Drop rows with invalid symbol/date
    df = df.dropna(subset=["symbol", "date"])

    # Drop duplicates keeping first
    dedup_cols = ["symbol", "date"]
    if "field_id" in df.columns:
        dedup_cols.append("field_id")
    if "data_type" in df.columns:
        dedup_cols.append("data_type")
    df = df.drop_duplicates(subset=[c for c in dedup_cols if c in df.columns], keep="first")

    return df


def compute_quarterly_fwd_ret(f10_dates: pd.DataFrame, horizon_days: int = 63) -> pd.DataFrame:
    """Compute forward returns aligned to F10 report dates.

    For each (symbol, F10_report_date), finds the nearest trading day,
    then computes fwd_ret = (close after N days - close on entry) / close on entry.

    Args:
        f10_dates: DataFrame with columns [symbol, date] — F10 report dates
        horizon_days: Number of calendar days forward (default 63 ≈ 1 quarter)

    Returns:
        DataFrame with columns [symbol, date, fwd_ret_5d, fwd_ret_20d, fwd_ret_quarterly]
        where date is the F10 report date (not the trading date).
    """
    import datetime
    client = bigquery.Client(project=PROJECT)

    # Get date range from F10 data
    f10_dates = f10_dates.copy(); f10_dates["date"] = pd.to_datetime(f10_dates["date"]); min_date = str(f10_dates["date"].min().date())
    max_date = f10_dates["date"].max()
    # Extend range to cover forward window
    end_date_val = pd.to_datetime(max_date) + datetime.timedelta(days=horizon_days + 30)

    # Load bar data for all symbols
    symbols = sorted(f10_dates["symbol"].unique().tolist())
    bq_symbols = [f"US.{s}" for s in symbols]

    query = f"""
        SELECT symbol, DATE(timestamp) as date, close
        FROM `{DATASET}.us_bars_1d`
        WHERE symbol IN UNNEST(@symbols)
          AND DATE(timestamp) BETWEEN '{pd.to_datetime(min_date).strftime("%Y-%m-%d")}' AND '{end_date_val.strftime("%Y-%m-%d")}'
        ORDER BY symbol, date
    """
    job_config = bigquery.QueryJobConfig(query_parameters=[
        bigquery.ArrayQueryParameter("symbols", "STRING", bq_symbols),
    ])
    bars = client.query(query, job_config=job_config).to_dataframe()
    bars["symbol"] = bars["symbol"].str.replace("US.", "")
    bars["date"] = pd.to_datetime(bars["date"])
    bars = bars.sort_values(["symbol", "date"])

    # Vectorized: use searchsorted on per-symbol bar index
    bars_indexed = bars.set_index(["symbol", "date"]).sort_index()
    results = []
    
    for sym in symbols:
        if sym not in bars_indexed.index.get_level_values(0):
            continue
        sym_bars = bars_indexed.loc[sym]
        sym_dates_idx = sym_bars.index
        sym_closes = sym_bars["close"].values
        
        sym_f10 = pd.DatetimeIndex(pd.to_datetime(
            f10_dates[f10_dates["symbol"] == sym]["date"].drop_duplicates()
        )).sort_values()
        
        if len(sym_f10) == 0 or len(sym_dates_idx) == 0:
            continue
        
        entry_positions = sym_dates_idx.searchsorted(sym_f10)
        valid_mask = entry_positions < len(sym_dates_idx)
        if not valid_mask.any():
            continue
        
        entry_positions = entry_positions[valid_mask]
        valid_f10 = sym_f10[valid_mask]
        entry_closes = sym_closes[entry_positions]
        entry_dates_arr = sym_dates_idx[entry_positions]
        
        forward_dates = entry_dates_arr + pd.Timedelta(days=horizon_days)
        forward_positions = sym_dates_idx.searchsorted(forward_dates)
        
        d5_arr = entry_dates_arr + pd.Timedelta(days=7)
        p5_arr = sym_dates_idx.searchsorted(d5_arr)
        
        d20_arr = entry_dates_arr + pd.Timedelta(days=30)
        p20_arr = sym_dates_idx.searchsorted(d20_arr)
        
        for i in range(len(valid_f10)):
            if forward_positions[i] >= len(sym_closes):
                continue
            fwd_ret = (sym_closes[forward_positions[i]] - entry_closes[i]) / entry_closes[i]
            fwd5 = (sym_closes[p5_arr[i]] - entry_closes[i]) / entry_closes[i] if p5_arr[i] < len(sym_closes) else None
            fwd20 = (sym_closes[p20_arr[i]] - entry_closes[i]) / entry_closes[i] if p20_arr[i] < len(sym_closes) else None
            results.append({
                "symbol": sym,
                "date": valid_f10[i],
                "fwd_ret_5d": fwd5,
                "fwd_ret_20d": fwd20,
                "fwd_ret_quarterly": fwd_ret,
            })
        
    result = pd.DataFrame(results)
    if not result.empty:
        result["date"] = pd.DatetimeIndex(pd.to_datetime(result["date"]))

    log.info("Quarterly fwd_ret: %d rows for %d symbols from %d F10 dates",
             len(result), result["symbol"].nunique(), len(f10_dates["date"].unique()))
    return result

def compute_ic(factor_values: pd.Series, fwd_ret: pd.Series) -> tuple[float, float, int]:
    """Calculate Spearman rank IC, t-statistic, and sample count.

    Returns (ic, t_stat, n).  Returns (NaN, NaN, 0) if insufficient data.
    """
    valid = pd.concat([factor_values, fwd_ret], axis=1).dropna()
    if len(valid) < 30:
        return float("nan"), float("nan"), len(valid)

    ic, _pval = spearmanr(valid.iloc[:, 0], valid.iloc[:, 1])
    n = len(valid)
    denom = np.sqrt(max(1 - ic ** 2, 1e-12))
    t_stat = abs(ic) * np.sqrt(n - 2) / denom
    return float(ic), float(t_stat), n


def main():
    parser = argparse.ArgumentParser(description="F10 Factor IC Evaluation")
    parser.add_argument("--start", default="2020-01-01")
    parser.add_argument("--end", default="2026-05-30")
    parser.add_argument("--register", action="store_true",
                        help="Register passing factors to BQ factor_registry")
    parser.add_argument("--label", default="fwd_ret_quarterly",
                        choices=["fwd_ret_5d", "fwd_ret_20d", "fwd_ret_quarterly"],
                        help="Forward return horizon for IC")
    args = parser.parse_args()

    # Thresholds
    MIN_ABS_IC = 0.05
    MIN_T_STAT = 3.0
    MIN_COVERAGE = 0.70
    MIN_SAMPLES = 10  # F10 is quarterly, not daily

    log.info("=" * 60)
    log.info("F10 Factor IC Evaluation")
    log.info("Period: %s -> %s  |  Label: %s", args.start, args.end, args.label)
    log.info("Criteria: |IC|>%.2f, |t|>%.1f, coverage>%.0f%%, n>=%d",
             MIN_ABS_IC, MIN_T_STAT, MIN_COVERAGE * 100, MIN_SAMPLES)
    log.info("=" * 60)

    # ── 1. Load & preprocess F10 tables ──
    data_map: dict[str, pd.DataFrame] = {}
    for tbl in F10_TABLES:
        try:
            raw = load_f10_table(tbl, args.start, args.end)
            if raw is None or raw.empty:
                continue
            processed = preprocess_table(tbl, raw)
            if processed.empty:
                log.warning("  %s: preprocessing yielded empty; skipping", tbl)
                continue
            key = TABLE_TO_KEY[tbl]
            data_map[key] = processed
            log.info("  -> data_map['%s']: %d rows x %d cols", key, len(processed), len(processed.columns))
        except Exception as e:
            log.warning("  %s: SKIP — %s", tbl, e)

    # Transform raw data to builder-compatible format
    data_map = F10Transformer.transform_all(data_map)

    # Set (symbol, date) MultiIndex on transformed DataFrames
    for key in list(data_map.keys()):
        df = data_map[key]
        # Deduplicate to avoid "Reindexing only valid with uniquely valued Index objects"
        df = df.drop_duplicates(subset=[c for c in ["symbol", "date"] if c in df.columns])
        if not df.empty and 'symbol' in df.columns and 'date' in df.columns:
            data_map[key] = df.set_index(['symbol', 'date'])

    if not data_map:
        log.error("No F10 data loaded after preprocessing!")
        return 1

    # ── 2. Compute quarterly forward returns aligned to F10 dates ──
    # Collect all unique (symbol, date) pairs from F10 data
    f10_dates_list = []
    for key, df in data_map.items():
        if df.empty:
            continue
        if isinstance(df.index, pd.MultiIndex) and df.index.nlevels >= 2:
            idx_frame = df.index.to_frame(index=False)
            if "symbol" in idx_frame.columns and "date" in idx_frame.columns:
                f10_dates_list.append(idx_frame[["symbol", "date"]])
        elif "symbol" in df.columns and "date" in df.columns:
            f10_dates_list.append(df[["symbol", "date"]])

    if f10_dates_list:
        f10_dates = pd.concat(f10_dates_list).drop_duplicates()
        log.info("F10 dates collected: %d unique (symbol,date) pairs", len(f10_dates))
    else:
        log.error("No F10 dates found!")
        return 1

    fwd = compute_quarterly_fwd_ret(f10_dates, horizon_days=63)

    # Use fwd_ret_quarterly as the primary label (63 calendar days ≈ 1 quarter)
    if fwd.empty:
        log.error("No forward return data computed!")
        return 1

    # ── 3. Build category map ──
    ffb = FundamentalFactorBuilder()
    category_map = _build_category_map(ffb)
    all_factors = ffb.ALL_FACTOR_COLS

    log.info("Evaluating IC for %d F10 factors ...", len(all_factors))

    # ── 4. Evaluate each factor ──
    results: list[dict] = []
    for factor_name in all_factors:
        try:
            # Only pass tables that contain this factor's category
            cat_map = {v:k for k,v in TABLE_TO_KEY.items()}
            sub_map = {k: data_map[k] for k in data_map if factor_name in data_map[k].columns}
            if not sub_map:
                # Fallback: try all tables
                sub_map = data_map
            factors_df = ffb.compute([factor_name], sub_map)
            if not factors_df.empty and not factors_df.index.is_unique:
                factors_df = factors_df[~factors_df.index.duplicated(keep="first")]
            if factors_df.empty or factor_name not in factors_df.columns:
                results.append({
                    "factor": factor_name,
                    "ic": float("nan"), "t_stat": float("nan"),
                    "coverage": 0, "n": 0,
                    "status": "no_data",
                })
                continue

            # compute() preserves (symbol, date) MultiIndex -> reset to columns
            factor_series = factors_df[factor_name]
            if isinstance(factor_series.index, pd.MultiIndex) and factor_series.index.nlevels >= 2:
                # MultiIndex: (symbol, date)
                merged = factor_series.reset_index()
                merged.columns = ["symbol", "date", factor_name]
            else:
                # Single index; try to infer from data_map
                merged = factor_series.reset_index()
                merged.columns = ["date", factor_name] if len(merged.columns) == 2 else list(merged.columns)
                # Don't have symbol — skip
                if "symbol" not in merged.columns:
                    results.append({
                        "factor": factor_name,
                        "ic": float("nan"), "t_stat": float("nan"),
                        "coverage": 0, "n": len(merged),
                        "status": "no_symbol",
                    })
                    continue

            # Merge with forward returns on (symbol, date)
            merged["date"] = pd.DatetimeIndex(pd.to_datetime(merged["date"], errors="coerce"))
            merged["date"] = pd.to_datetime(merged["date"]).dt.normalize()
            fwd["date"] = pd.to_datetime(fwd["date"]).dt.normalize()
            merged = merged.merge(fwd, on=["symbol", "date"], how="inner")

            if len(merged) < MIN_SAMPLES:
                results.append({
                    "factor": factor_name,
                    "ic": float("nan"), "t_stat": float("nan"),
                    "coverage": 0, "n": len(merged),
                    "status": "too_few",
                })
                continue

            total_rows = len(merged)
            ic, t_stat, n = compute_ic(merged[factor_name], merged[args.label])
            coverage = n / total_rows if total_rows > 0 else 0

            passes = (
                not np.isnan(ic) and abs(ic) > MIN_ABS_IC
                and not np.isnan(t_stat) and abs(t_stat) > MIN_T_STAT
                and coverage > MIN_COVERAGE
            )
            status = "pass" if passes else "fail"
            results.append({
                "factor": factor_name,
                "ic": ic, "t_stat": t_stat,
                "coverage": coverage, "n": n,
                "status": status,
            })

            if status == "pass":
                log.info("  ✅ %-30s IC=%+7.4f  t=%6.1f  cov=%5.1f%%  n=%d",
                         factor_name, ic, t_stat, coverage * 100, n)

        except Exception as e:
            log.error("  ❌ %s: %s", factor_name, e)
            results.append({
                "factor": factor_name,
                "ic": float("nan"), "t_stat": float("nan"),
                "coverage": 0, "n": 0,
                "status": f"error: {e}",
            })

    # ── 5. Summary ──
    df_results = pd.DataFrame(results)
    too_few_df = df_results[df_results["status"] == "too_few"]
    # Debug: status distribution
    status_counts = df_results['status'].value_counts()
    log.info('Status distribution: %s', dict(status_counts))
    passing = df_results[df_results["status"] == "pass"]
    failed = df_results[df_results["status"] == "fail"]
    no_data = df_results[df_results["status"] == "no_data"]
    errors = df_results[df_results["status"].str.startswith("error", na=False)]

    print("\n" + "=" * 70)
    print("  📊  F10 Factor IC Evaluation Results")
    print("=" * 70)
    print(f"  Total factors evaluated : {len(df_results)}")
    print(f"  ✅ Passing              : {len(passing)}")
    print(f"  ❌ Failed threshold     : {len(failed)}")
    not_enough = len(no_data) + len(too_few_df)
    print(f"  ⚠️  No data / too few    : {not_enough} (no_data={len(no_data)}, too_few={len(too_few_df)})")
    print(f"  💥 Errors               : {len(errors)}")

    if len(failed) > 0:
        print()
        print('  Failed (below threshold IC):')
        for _, row in failed.sort_values("ic", key=abs, ascending=False).head(10).iterrows():
            ic_v = row.get("ic", 0) or 0
            print(f"  {row['factor']:30s} IC={ic_v:+.4f}")
    if len(passing) > 0:
        print(f"\n  ── Passing factors (|IC|>{MIN_ABS_IC}, |t|>{MIN_T_STAT}, "
              f"cov>{MIN_COVERAGE:.0%}) ──")
        sorted_passing = passing.sort_values("ic", key=abs, ascending=False)
        for _, row in sorted_passing.iterrows():
            print(f"  {row['factor']:30s}  IC={row['ic']:+7.4f}  "
                  f"t={row['t_stat']:6.1f}  cov={row['coverage']:5.1%}  "
                  f"n={row['n']:,}  cat={category_map.get(row['factor'], '?')}")

    if len(errors) > 0:
        print(f"\n  ── Errors ──")
        for _, row in errors.iterrows():
            print(f"  {row['factor']:30s}  {row['status']}")

    # ── 6. Register passing factors ──
    if args.register and len(passing) > 0:
        log.info("Registering %d passing factors to BQ factor_registry ...", len(passing))
        registry = FactorRegistry()
        registered = 0
        for _, row in passing.iterrows():
            name = row["factor"]
            cat = category_map.get(name, "unknown")
            ok = registry.register(
                factor_id=f"us_{name}",
                name=name.replace("_", " ").title(),
                market="us",
                source="fundamental",
                category=cat,
                formula=f"factors/fundamental_builder.py::{name}",
                description=(
                    f"F10 {cat} factor. IC={row['ic']:+.4f}, "
                    f"t={row['t_stat']:.1f}, cov={row['coverage']:.1%}"
                ),
            )
            if ok:
                registered += 1
        log.info("Registered %d/%d factors successfully", registered, len(passing))

    print("=" * 70)

    # Return non-zero if too few passing (for CI visibility)
    if len(passing) < 5:
        log.warning("Only %d/41 factors passed — F10 signal may be weak", len(passing))
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
