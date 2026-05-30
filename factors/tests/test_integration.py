"""End-to-end integration test for FactorBuilder with real HK market data."""
from __future__ import annotations

import glob
import os

import numpy as np
import pandas as pd
import pytest

from factors.builder import FactorBuilder

# ── Constants ─────────────────────────────────────────────────────────

DATA_ROOT = "data/historical/hk/raw/hk/bars/freq=1d"

# Expected columns from FactorBuilder (39 factors + 2 labels)
EXPECTED_FACTOR_COLS = [
    "ret_1d", "ret_5d", "ret_10d", "ret_20d", "ret_60d", "ret_120d",
    "vol_5d", "vol_10d", "vol_20d", "vol_60d",
    "vol_ratio_5d", "vol_ratio_20d", "corr_vp_20d", "vol_trend",
    "rsi_14", "macd", "macd_signal", "macd_hist",
    "bb_position", "bb_width", "price_position_20d", "streak",
    "avg_turnover_5d", "avg_turnover_20d", "turnover_ratio", "turnover_growth",
    "daily_range", "upper_shadow_ratio", "lower_shadow_ratio", "gap", "vp_divergence",
    "skew_20d", "kurt_20d", "skew_60d", "kurt_60d", "skew_120d", "kurt_120d",
    "low_vol_proxy", "price_stability",
]
EXPECTED_LABEL_COLS = ["fwd_ret_5d", "fwd_ret_20d"]
ALL_EXPECTED_COLS = EXPECTED_FACTOR_COLS + EXPECTED_LABEL_COLS


def discover_symbols(min_files: int = 300) -> list[str]:
    """Scan HK data directory and return symbols with >= *min_files* parquet files."""
    pattern = os.path.join(DATA_ROOT, "year=*/month=*/day=*/symbol=*.parquet")
    files = glob.glob(pattern)
    sym_files: dict[str, int] = {}
    for fp in files:
        sym = fp.split("symbol=")[-1].replace(".parquet", "")
        sym_files[sym] = sym_files.get(sym, 0) + 1
    return sorted(s for s, c in sym_files.items() if c >= min_files)


def preload_all_hk_symbols(
    symbols: list[str],
    start: str,
    end: str,
) -> dict[str, pd.DataFrame]:
    """Efficiently pre-load OHLCV data for all given symbols in one pass.

    Groups parquet files by symbol from the glob, then reads each symbol's
    files, filters by date range, and returns a dict of ``symbol -> DataFrame``.

    Much faster than calling ``data_loader()`` per symbol due to single glob scan.
    """
    start_dt = pd.Timestamp(start).tz_localize("UTC")
    end_dt = pd.Timestamp(end).tz_localize("UTC")
    symbol_set = set(symbols)

    pattern = os.path.join(DATA_ROOT, "year=*/month=*/day=*/symbol=*.parquet")
    files = sorted(glob.glob(pattern))

    # Group file paths by symbol
    sym_files: dict[str, list[str]] = {}
    for fp in files:
        sym = fp.split("symbol=")[-1].replace(".parquet", "")
        if sym in symbol_set:
            sym_files.setdefault(sym, []).append(fp)

    loaded: dict[str, pd.DataFrame] = {}
    for sym, fps in sym_files.items():
        rows: list[pd.DataFrame] = []
        for fp in fps:
            df = pd.read_parquet(fp)
            ts = df["timestamp"].iloc[0]
            if start_dt <= ts <= end_dt:
                rows.append(df)

        if not rows:
            continue

        combined = pd.concat(rows, ignore_index=True)
        combined["date"] = combined["timestamp"].dt.tz_localize(None)
        result = combined[["date", "symbol", "open", "high", "low", "close", "volume"]].copy()
        result = result.sort_values("date").reset_index(drop=True)
        loaded[sym] = result

    return loaded


# ── Helper: column checks ─────────────────────────────────────────────

def _assert_expected_columns(result: pd.DataFrame):
    assert "symbol" in result.columns
    assert "date" in result.columns
    for col in ALL_EXPECTED_COLS:
        assert col in result.columns, f"Missing expected column: {col}"
    assert len(result.columns) >= 43, (
        f"Expected >=43 columns, got {len(result.columns)}"
    )


# ── Integration test ──────────────────────────────────────────────────

def test_factor_builder_with_real_hk_data():
    """End-to-end integration test: FactorBuilder with real HK market data."""
    # ── 1. Discover symbols ──────────────────────────────────────────
    all_symbols = discover_symbols(min_files=300)
    if len(all_symbols) < 3:
        pytest.skip(f"Not enough HK symbols found (need >=3, got {len(all_symbols)})")
    print(f"Total symbols available with >=300 files: {len(all_symbols)}")

    # ── 2. Pre-load all symbols efficiently ──────────────────────────
    # Use up to 30 symbols for IC analysis (if available)
    n_ic_symbols = min(len(all_symbols), 30)
    symbols_to_load = all_symbols[:n_ic_symbols]
    print(f"Pre-loading data for {len(symbols_to_load)} symbols from 2023-06-01 to 2024-05-31...")
    data_cache = preload_all_hk_symbols(symbols_to_load, "2023-06-01", "2024-05-31")
    print(f"Loaded {len(data_cache)} symbols with data")

    if len(data_cache) < 3:
        pytest.skip(f"Not enough symbols with data in range (need >=3, got {len(data_cache)})")

    # ── 3. Pick symbols and build data_loader from cache ─────────────
    # Pick 3 main symbols that have >=200 rows
    loaded_syms = sorted(data_cache.keys())
    main_syms = loaded_syms[:3]
    print(f"Main test symbols: {main_syms}")

    for sym in main_syms:
        print(f"  {sym}: {len(data_cache[sym])} rows loaded")

    # Build a data_loader that uses the cache
    def cached_loader(symbol: str, start: str, end: str) -> pd.DataFrame:
        df = data_cache.get(symbol)
        if df is None or df.empty:
            return pd.DataFrame()
        return df

    # ── 4. Instantiate FactorBuilder ─────────────────────────────────
    fb = FactorBuilder()

    # ── 5. Build factor dataset (3 main symbols) ──────────────────────
    result = fb.build_factor_dataset(
        main_syms,
        "2023-06-01",
        "2024-05-31",
        cached_loader,
    )

    assert not result.empty, "Factor dataset is empty (no symbols passed the 200-row minimum)"
    _assert_expected_columns(result)
    print(f"Factor dataset shape: {result.shape}")
    print(f"Unique symbols in dataset: {result['symbol'].unique()}")

    for sym in main_syms:
        n_rows = len(result[result["symbol"] == sym])
        assert n_rows > 0, f"Symbol {sym} has no rows in dataset"
        print(f"  {sym}: {n_rows} rows in factor dataset")

    # No all-NaN columns
    for col in EXPECTED_FACTOR_COLS:
        assert col in result.columns, f"Missing factor column: {col}"
        null_pct = result[col].isna().mean()
        assert null_pct < 1.0, f"Column {col} is all NaN"

    # ── 6. Process factors ──────────────────────────────────────────
    processed = fb.process_factors(result)

    for col in EXPECTED_FACTOR_COLS:
        mean_val = processed[col].mean()
        assert abs(mean_val) < 0.5, f"Factor {col} mean={mean_val:.4f}, expected ≈ 0"

    for col in EXPECTED_FACTOR_COLS:
        std_val = processed[col].std()
        assert abs(std_val - 1.0) < 0.5, f"Factor {col} std={std_val:.4f}, expected ≈ 1"

    factor_cols_in_processed = [c for c in EXPECTED_FACTOR_COLS if c in processed.columns]
    nan_count = processed[factor_cols_in_processed].isna().sum().sum()
    assert nan_count == 0, f"Found {nan_count} NaN values after processing"

    # ── 7. Build cross-sectional dataset for IC ──────────────────────
    # Use all loaded symbols that have >=200 rows
    ic_symbols = [s for s in loaded_syms if len(data_cache[s]) >= 200]
    print(f"IC test: {len(ic_symbols)} symbols with >=200 rows")

    if len(ic_symbols) >= 30:
        ic_result = fb.build_factor_dataset(
            ic_symbols,
            "2023-06-01",
            "2024-05-31",
            cached_loader,
        )

        if not ic_result.empty:
            ic_processed = fb.process_factors(ic_result)
            ic_df = fb.compute_ic(ic_processed)

            assert not ic_df.empty, "IC DataFrame is empty"
            assert "date" in ic_df.columns
            assert "factor" in ic_df.columns
            assert "rank_ic" in ic_df.columns
            print(f"IC DataFrame: {len(ic_df)} rows, {ic_df['factor'].nunique()} factors")

            # At least some factors have non-zero mean IC
            factor_ic_means = ic_df.groupby("factor")["rank_ic"].mean()
            n_nonzero = (factor_ic_means.abs() > 1e-6).sum()
            assert n_nonzero > 0, "All factors have zero mean IC"
            print(f"Factors with non-zero mean IC: {n_nonzero}/{len(factor_ic_means)}")

            # ── 8. IC Summary ─────────────────────────────────────
            summary = fb.ic_summary(ic_df)
            assert not summary.empty, "IC summary is empty"
            expected_summary_cols = {"factor", "mean", "std", "count", "icir", "abs_mean_ic"}
            assert expected_summary_cols.issubset(set(summary.columns))
            abs_means = summary["abs_mean_ic"].values
            for i in range(len(abs_means) - 1):
                assert abs_means[i] >= abs_means[i + 1], (
                    "IC summary not sorted by abs_mean_ic descending"
                )
            print(f"IC summary: top 5 factors (by abs mean IC):")
            print(summary.head(5).to_string(index=False))
        else:
            print("IC analysis skipped: no symbols passed the 200-row minimum in batch build")
    else:
        print(f"IC analysis skipped: need >=30 symbols for cross-sectional IC, got {len(ic_symbols)}")

def test_full_pipeline_register_evaluate_query():
    """End-to-end: register factor, evaluate, query active."""
    from factors.builder import FactorBuilder
    from factors.evaluation import evaluate_factor

    np.random.seed(42)
    fb = FactorBuilder()
    df = pd.DataFrame({
        "date": pd.date_range("2020-01-01", periods=500, freq="B"),
        "open": 100 + np.cumsum(np.random.randn(500) * 0.5),
        "high": 102 + np.cumsum(np.random.randn(500) * 0.5),
        "low": 98 + np.cumsum(np.random.randn(500) * 0.5),
        "close": 101 + np.cumsum(np.random.randn(500) * 0.5),
        "volume": 1_000_000 + np.cumsum(np.random.randint(-10000, 10000, 500)),
    })
    all_factors = fb.compute_factors(df)

    # Evaluate momentum factor
    result = evaluate_factor(
        factor_values=all_factors["ret_20d"],
        fwd_ret_1d=all_factors["fwd_ret_5d"],
        fwd_ret_5d=all_factors["fwd_ret_5d"],
        fwd_ret_20d=all_factors["fwd_ret_20d"],
    )

    assert "ic_mean" in result
    assert "ic_tstat" in result
    assert "coverage" in result
    assert "passes_admission" in result
    assert isinstance(result["passes_admission"], bool)
    assert isinstance(result["ic_decay_1d"], float) or result["ic_decay_1d"] is None
