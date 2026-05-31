#!/usr/bin/env python3.12
"""Batch compute tech + fundamental factors and write to BigQuery factor_values.

Populates the factor_values table for ML training.

Usage:
    python scripts/compute_factors_batch.py --source tech --start 2020-01-01 --end 2026-05-30
    python scripts/compute_factors_batch.py --source fundamental --start 2024-01-01 --end 2026-05-30
    python scripts/compute_factors_batch.py --source all
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import argparse
import logging
from datetime import datetime, timezone

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
    # Strip US. prefix
    df["symbol"] = df["symbol"].str.replace("US.", "", regex=False)
    log.info("  Loaded %d rows, %d unique symbols", len(df), df["symbol"].nunique())
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
            rows.append(
                {
                    "factor_id": f"{market}_{col}",
                    "symbol": symbol,
                    "date": date_str,
                    "value": float(val),
                    "source_builder": source,
                    "computed_at": datetime.now(timezone.utc).isoformat(),
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
        stock_df = group.sort_values("date").drop_duplicates(subset=["date"]).reset_index(drop=True)
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

    n_written = 0
    if write_to_bq:
        n_written = write_factor_values(processed, "tech", tfb.factor_names, market)

    return len(tfb.factor_names), n_written


# ── Fundamental factors ─────────────────────────────────────────────────


def compute_fundamental_factors(
    start: str,
    end: str,
    market: str = "us",
    write_to_bq: bool = True,
) -> tuple[int, int]:
    """Compute fundamental factors from F10 BQ tables and write to factor_values.

    Loads F10 tables (financials, valuation, analyst, capital_flow, shareholder)
    via bulk BQ queries, preprocesses (JSON expansion, valuation pivot), and
    computes factors per symbol.

    Returns (num_factors, num_values_written).
    """
    import json

    ffb = FundamentalFactorBuilder()
    client = bigquery.Client(project=PROJECT)

    # Get symbols from bars_1d for the date range
    syms_query = f"""
        SELECT DISTINCT REPLACE(symbol, 'US.', '') as symbol
        FROM `{PROJECT}.quant.{market}_bars_1d`
        WHERE DATE(timestamp) BETWEEN @start AND @end
        ORDER BY symbol
    """
    syms_job = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("start", "STRING", start),
            bigquery.ScalarQueryParameter("end", "STRING", end),
        ],
    )
    syms_df = client.query(syms_query, job_config=syms_job).to_dataframe()
    symbols = syms_df["symbol"].tolist()
    log.info("Found %d symbols with OHLCV data in date range", len(symbols))

    if not symbols:
        return 0, 0

    # ── Helpers ──

    def _expand_json(df: pd.DataFrame) -> pd.DataFrame:
        if "data" not in df.columns:
            return df
        parsed = df["data"].apply(
            lambda v: json.loads(v) if isinstance(v, str) else (v if isinstance(v, dict) else {})
        )
        expanded = pd.DataFrame(parsed.tolist(), index=df.index)
        meta_cols = [c for c in df.columns if c != "data"]
        return pd.concat([df[meta_cols], expanded], axis=1)

    def _strip_prefix(df: pd.DataFrame) -> pd.DataFrame:
        if "symbol" in df.columns:
            df["symbol"] = df["symbol"].astype(str).str.replace("US.", "", regex=False)
        return df

    # ── Bulk load F10 tables ──

    raw_data: dict[str, pd.DataFrame] = {}

    # Financials
    fin_query = f"""
        SELECT * FROM `{PROJECT}.quant.{market}_financials`
        WHERE symbol IN UNNEST(@syms)
    """
    fin_job = bigquery.QueryJobConfig(
        query_parameters=[bigquery.ArrayQueryParameter("syms", "STRING", symbols)],
    )
    financials = client.query(fin_query, job_config=fin_job).to_dataframe()
    if not financials.empty:
        financials = _expand_json(financials)
        if "date_time_str" in financials.columns:
            financials["date"] = pd.to_datetime(
                financials["date_time_str"], format="%Y/%m/%d", errors="coerce"
            )
        financials = _strip_prefix(financials)
        raw_data["financials"] = financials
        log.info("financials: %d rows", len(financials))

    # Aux tables
    for table_name, key in [
        (f"{market}_valuation", "valuation"),
        (f"{market}_capital_flow", "capital_flow"),
        (f"{market}_analyst", "analyst"),
        (f"{market}_shareholder", "short_interest"),
    ]:
        try:
            aux_query = f"""
                SELECT * FROM `{PROJECT}.quant.{table_name}`
                WHERE symbol IN UNNEST(@syms)
            """
            aux_job = bigquery.QueryJobConfig(
                query_parameters=[bigquery.ArrayQueryParameter("syms", "STRING", symbols)],
            )
            aux_df = client.query(aux_query, job_config=aux_job).to_dataframe()
            if aux_df.empty:
                continue

            # Preprocess
            if table_name in (
                f"{market}_analyst",
                f"{market}_capital_flow",
                f"{market}_shareholder",
            ):
                aux_df = _expand_json(aux_df)
            elif table_name == f"{market}_valuation":
                if "valuation_type" in aux_df.columns and "value" in aux_df.columns:
                    aux_df["date"] = pd.to_datetime(aux_df["date"], errors="coerce").dt.date
                    aux_df = aux_df.pivot_table(
                        index=["symbol", "date"],
                        columns="valuation_type",
                        values="value",
                        aggfunc="first",
                    ).reset_index()

            aux_df = _strip_prefix(aux_df)
            raw_data[key] = aux_df
            log.info("%s: %d rows", table_name, len(aux_df))
        except Exception as e:
            log.warning("Load %s failed: %s", table_name, e)

    if "financials" not in raw_data:
        log.warning("No financials data loaded; cannot compute fundamental factors")
        return 0, 0

    # ── Compute factors per symbol ──

    all_frames: list[pd.DataFrame] = []
    for sym in symbols:
        try:
            sym_data: dict[str, pd.DataFrame] = {}
            for key, df in raw_data.items():
                sym_df = df[df["symbol"] == sym].copy()
                if sym_df.empty:
                    continue
                sym_data[key] = sym_df

            if "financials" not in sym_data:
                continue

            factors = ffb.compute(ffb.ALL_FACTOR_COLS, sym_data)
            if factors.empty:
                continue

            factors["symbol"] = sym
            fin_sym = sym_data["financials"]
            if "date" in fin_sym.columns:
                n = len(factors)
                factors["date"] = fin_sym["date"].values[:n]
            else:
                continue

            all_frames.append(factors)
        except Exception as e:
            log.warning("  %s: fundamental factor compute failed — %s", sym, e)

    if not all_frames:
        log.warning("No fundamental factors computed for any symbol")
        return 0, 0

    combined = pd.concat(all_frames, ignore_index=True)
    log.info(
        "Fundamental factors: %d rows x %d symbols, %d factor cols",
        len(combined),
        combined["symbol"].nunique(),
        len(ffb.factor_names),
    )

    # Process: winsorize + normalize
    processed = ffb.process_factors(combined, winsor_pct=0.01)
    processed["symbol"] = combined["symbol"].values
    processed["date"] = combined["date"].values

    n_written = 0
    if write_to_bq:
        n_written = write_factor_values(
            processed, "fundamental", ffb.factor_names, market
        )

    return len(ffb.factor_names), n_written


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
        "--start", default="2020-01-01", help="Start date YYYY-MM-DD"
    )
    parser.add_argument(
        "--end", default="2026-05-30", help="End date YYYY-MM-DD"
    )
    parser.add_argument(
        "--market", default="us", help="Market code (default: us)"
    )
    parser.add_argument(
        "--no-write",
        action="store_true",
        help="Compute but do not write to BQ (dry-run)",
    )
    args = parser.parse_args()

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
