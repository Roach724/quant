#!/usr/bin/env python3.12
"""Batch compute tech + fundamental factors and write to BigQuery factor_values.

Populates the factor_values table for ML training.

Usage:
    python scripts/compute_factors_batch.py --source tech --start 2020-01-01 --end 2026-05-30
    python scripts/compute_factors_batch.py --source fundamental --start 2024-01-01 --end 2026-05-30
    python scripts/compute_factors_batch.py --source all
    python scripts/compute_factors_batch.py --source tech --incremental
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import argparse
import logging
from datetime import datetime, timezone, timedelta

import numpy as np
import pandas as pd
from google.cloud import bigquery

from factors.tech_builder import TechFactorBuilder
from factors.fundamental_builder import FundamentalFactorBuilder

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s"
)
log = logging.getLogger("compute_factors")

PROJECT = "deductive-notch-495015-c2"
FACTOR_VALUES_TABLE = f"{PROJECT}.quant.factor_values"
BQ_TIMEOUT_SECONDS = 600


def load_registry_factors(source: str = None, market: str = "us") -> list[str]:
    """Load active factor IDs from factor_registry.
    
    Args:
        source: Filter by source_builder ('tech', 'fundamental', or None for all)
        market: Filter by market
    
    Returns:
        List of factor_id strings (e.g., ['us_ret_1d', 'us_vol_20d', ...])
    """
    client = bigquery.Client(project=PROJECT)
    source_filter = f"AND source = '{source}'" if source else ""
    query = f"""
        SELECT factor_id FROM `{PROJECT}.quant.factor_registry`
        WHERE is_active = TRUE AND market = '{market}' {source_filter}
        ORDER BY factor_id
    """
    df = client.query(query).to_dataframe()
    factor_ids = df["factor_id"].tolist()
    log.info("Registry: %d active factors for source=%s", len(factor_ids), source or "all")
    return factor_ids


# ── Helpers ────────────────────────────────────────────────────────────


def load_ohlcv_from_bq(market: str, start: str, end: str) -> pd.DataFrame:
    """Load daily OHLCV from BigQuery bars_1d table.

    Returns a DataFrame with columns: symbol, date, open, high, low, close, volume.
    """
    client = bigquery.Client(project=PROJECT)
    table = f"{PROJECT}.quant.{market}_bars_1d"

    query = f"""
        SELECT symbol, DATE(timestamp) as date,
               open, high, low, close, volume
        FROM `{table}`
        WHERE DATE(timestamp) BETWEEN @start AND @end
        ORDER BY symbol, date
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("start", "STRING", start),
            bigquery.ScalarQueryParameter("end", "STRING", end),
        ],
    )
    log.info("Loading OHLCV from %s...", table)
    df = client.query(query, job_config=job_config).to_dataframe()

    # Normalize symbols to canonical bare format
    from common.normalize import normalize_symbol_series
    df["symbol"] = normalize_symbol_series(df["symbol"], market)
    log.info("  Loaded %d rows, %d unique symbols", len(df), df["symbol"].nunique())
    # Fix: convert dbdate to datetime64 for TechFactorBuilder compatibility
    df["date"] = pd.to_datetime(df["date"])
    return df


def write_factor_values(
    df: pd.DataFrame,
    source: str,
    factor_names: list[str],
    market: str,
) -> int:
    """Write factor values to BigQuery factor_values table.

    Converts wide-format DataFrame (symbol + date + factor columns) into
    long-format rows suitable for the factor_values table schema.

    Returns the number of rows written.
    """
    client = bigquery.Client(project=PROJECT)

    rows: list[dict] = []
    for _, row in df.iterrows():
        symbol = str(row.get("symbol", ""))
        date_val = row.get("date", "")
        if isinstance(date_val, pd.Timestamp):
            date_str = date_val.strftime("%Y-%m-%d")
        elif hasattr(date_val, "isoformat"):
            date_str = date_val.isoformat()[:10]
        else:
            date_str = str(date_val)[:10]

        for col in factor_names:
            if col not in df.columns:
                continue
            val = row[col]
            if pd.isna(val):
                continue
            if np.isinf(float(val)):
                continue
            rows.append(
                {
                    "factor_id": f"{market}_{col}",
                    "symbol": symbol,
                    "date": date_str,
                    "value": float(val),
                    "source_builder": source,
                }
            )

    if not rows:
        log.warning("No factor values to write for source=%s", source)
        return 0

    # Insert in chunks to avoid large payloads
    chunk_size = 10_000
    total_inserted = 0
    for i in range(0, len(rows), chunk_size):
        chunk = rows[i : i + chunk_size]
        errors = client.insert_rows_json(FACTOR_VALUES_TABLE, chunk)
        if errors:
            log.error("Insert errors (first 3): %s", errors[:3])
        else:
            total_inserted += len(chunk)
            log.info(
                "  Inserted chunk %d/%d (%d rows)",
                i // chunk_size + 1,
                (len(rows) + chunk_size - 1) // chunk_size,
                len(chunk),
            )

    log.info(
        "Wrote %d factor values to %s (source=%s, market=%s)",
        total_inserted,
        FACTOR_VALUES_TABLE,
        source,
        market,
    )
    return total_inserted


# ── Tech factors ────────────────────────────────────────────────────────


def compute_tech_factors(
    start: str,
    end: str,
    market: str = "us",
    write_to_bq: bool = True,
) -> tuple[int, int]:
    """Compute tech factors from OHLCV and write to factor_values.

    Returns (num_factors, num_values_written).
    """
    df = load_ohlcv_from_bq(market, start, end)
    if df.empty:
        log.warning("No OHLCV data found for %s %s-%s", market, start, end)
        return 0, 0

    tfb = TechFactorBuilder()
    all_frames: list[pd.DataFrame] = []

    for sym, group in df.groupby("symbol"):
        stock_df = group.sort_values("date")
        stock_df["date"] = pd.to_datetime(stock_df["date"])
        stock_df = stock_df.drop_duplicates(subset=["date"]).reset_index(drop=True)
        try:
            factors = tfb.compute_factors(stock_df)
            if factors is not None and not factors.empty:
                factors["symbol"] = sym
                n = len(factors)
                factors["date"] = stock_df["date"].values[:n]
                all_frames.append(factors)
        except Exception as e:
            log.warning("  %s: tech factor compute failed — %s", sym, e)

    if not all_frames:
        log.warning("No tech factors computed for any symbol")
        return 0, 0

    combined = pd.concat(all_frames, ignore_index=True)
    log.info(
        "Tech factors: %d rows x %d symbols, %d factor cols",
        len(combined),
        combined["symbol"].nunique(),
        len(tfb.factor_names),
    )

    # Process: winsorize + normalize cross-sectionally
    processed = tfb.process_factors(combined, winsor_pct=0.01)

    # Add back symbol and date (process_factors may strip them)
    processed["symbol"] = combined["symbol"].values
    processed["date"] = combined["date"].values

    # Filter to only registry-registered factors
    registry_factors = load_registry_factors(source="tech", market=market)
    write_names = [f for f in tfb.factor_names if f"{market}_{f}" in set(registry_factors)]
    log.info("Writing %d/%d factors (registry-filtered)", len(write_names), len(tfb.factor_names))

    n_written = 0
    if write_to_bq and write_names:
        n_written = write_factor_values(processed, "tech", write_names, market)

    return len(tfb.factor_names), n_written


# ── Fundamental factors ─────────────────────────────────────────────────


def compute_fundamental_factors(
    start: str,
    end: str,
    market: str = "us",
    write_to_bq: bool = True,
) -> tuple[int, int]:
    """Compute fundamental factors using F10Transformer pipeline.

    This delegates to the verified pipeline used in evaluate_f10_factors.py.
    """
    from scripts.evaluate_f10_factors import load_f10_table, preprocess_table, TABLE_TO_KEY
    from factors.f10_transformer import F10Transformer
    
    ffb = FundamentalFactorBuilder()
    f10_tables = [f'us_valuation', f'us_financials', f'us_analyst', f'us_capital_flow', f'us_shareholder']
    
    data_map = {}
    for tbl in f10_tables:
        raw = load_f10_table(tbl, start, end)
        if raw is not None and not raw.empty:
            processed = preprocess_table(tbl, raw)
            if not processed.empty:
                data_map[TABLE_TO_KEY[tbl]] = processed
    
    if not data_map:
        log.warning("No F10 data for %s-%s", start, end)
        return 0, 0
    
    data_map = F10Transformer.transform_all(data_map)
    
    for k in list(data_map.keys()):
        df = data_map[k]
        if not df.empty and 'symbol' in df.columns and 'date' in df.columns:
            df = df.drop_duplicates(subset=['symbol', 'date'])
            data_map[k] = df.set_index(['symbol', 'date'])
    
    cat_keys = {
        'financials': ffb.QUALITY_COLS + ffb.GROWTH_COLS + ffb.EARNINGS_QUALITY_COLS,
        'valuation': ffb.VALUATION_COLS,
        'short_interest': ffb.SHORT_COLS + ffb.SMART_MONEY_COLS,
        'capital_flow': ffb.FLOW_COLS,
        'analyst': ffb.ANALYST_COLS,
        'earnings_events': ffb.EARNINGS_EVENT_COLS,
    }
    
    merged = None
    all_factor_names = []
    
    for key, cat_cols in cat_keys.items():
        df = data_map.get(key)
        if df is None or df.empty:
            continue
        available = [c for c in cat_cols if c in df.columns]
        if not available:
            continue
        part = ffb.compute(available, {key: df})
        if part.empty:
            continue
        part = part[~part.index.duplicated(keep='first')].reset_index()
        if 'date' in part.columns:
            part['date'] = pd.to_datetime(part['date'], errors='coerce')
        part = part.drop_duplicates(subset=['symbol', 'date'])
        for c in part.columns:
            if part[c].dtype.name == 'Int64':
                part[c] = part[c].astype(float)
        if merged is None:
            merged = part
        else:
            if 'date' in merged.columns:
                merged['date'] = pd.to_datetime(merged['date'], errors='coerce')
            merged = merged.merge(part, on=['symbol', 'date'], how='outer')
        all_factor_names.extend(available)
    
    if merged is None:
        return 0, 0
    
    factor_cols = [c for c in merged.columns if c not in ('symbol', 'date')]
    for c in factor_cols:
        merged[c] = merged[c].astype(float)
    
    log.info("Fundamental factors: %d rows x %d cols", *merged.shape)
    processed = ffb.process_factors(merged, winsor_pct=0.01)
    processed['symbol'] = merged['symbol'].values
    processed['date'] = merged['date'].values
    
    # Registry filter
    registry_factors = load_registry_factors(source="fundamental", market=market)
    write_names = [f for f in all_factor_names if f"{market}_{f}" in set(registry_factors)]
    log.info("Writing %d/%d factors (registry-filtered)", len(write_names), len(all_factor_names))
    
    n_written = 0
    if write_to_bq and write_names:
        n_written = write_factor_values(processed, "fundamental", write_names, market)
    
    return len(write_names), n_written


# ── Main ────────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(
        description="Batch compute factors and write to BigQuery factor_values"
    )
    parser.add_argument(
        "--source",
        choices=["tech", "fundamental", "all"],
        default="all",
        help="Factor source to compute (default: all)",
    )
    parser.add_argument(
        "--start", default=None, help="Start date YYYY-MM-DD (default: 30 days ago)"
    )
    parser.add_argument(
        "--end", default=None, help="End date YYYY-MM-DD (default: today)"
    )
    parser.add_argument(
        "--market", default="us", help="Market code (default: us)"
    )
    parser.add_argument(
        "--no-write",
        action="store_true",
        help="Compute but do not write to BQ (dry-run)",
    )
    parser.add_argument("--incremental", action="store_true",
                        help="Incremental mode: only compute last 30 days")
    args = parser.parse_args()

    # Dynamic defaults: today for end, 30 days ago for start
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if args.end is None:
        args.end = today
    if args.start is None:
        args.start = (datetime.now(timezone.utc) - timedelta(days=30)).strftime("%Y-%m-%d")

    if args.incremental:
        end_dt = datetime.strptime(args.end, "%Y-%m-%d")
        args.start = (end_dt - timedelta(days=14)).strftime("%Y-%m-%d")
        log.info("Incremental mode: computing %s → %s (last 14 days)", args.start, args.end)

    log.info("=" * 60)
    log.info("Batch Factor Computation")
    log.info(
        "Source: %s | %s → %s | Market: %s",
        args.source,
        args.start,
        args.end,
        args.market,
    )
    log.info("=" * 60)

    total_factors = 0
    total_values = 0

    if args.source in ("tech", "all"):
        n_factors, n_values = compute_tech_factors(
            args.start,
            args.end,
            args.market,
            write_to_bq=not args.no_write,
        )
        log.info("Tech factors: %d computed, %d values written", n_factors, n_values)
        total_factors += n_factors
        total_values += n_values

    if args.source in ("fundamental", "all"):
        n_factors, n_values = compute_fundamental_factors(
            args.start,
            args.end,
            args.market,
            write_to_bq=not args.no_write,
        )
        log.info(
            "Fundamental factors: %d computed, %d values written",
            n_factors,
            n_values,
        )
        total_factors += n_factors
        total_values += n_values

    log.info("=" * 60)
    log.info(
        "Done. Total: %d factors, %d values written",
        total_factors,
        total_values,
    )


if __name__ == "__main__":
    main()
