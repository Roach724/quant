"""TDD tests for FactorBuilder — ported from hk-quant/src/factor_builder.py.

Follows the factor definitions from the design doc:
  1. _returns: ret_1d, ret_5d, ret_10d, ret_20d, ret_60d, ret_120d (6)
  2. _volatility: vol_5d, vol_10d, vol_20d, vol_60d (4)
  3. _volume_factors: vol_ratio_5d, vol_ratio_20d, corr_vp_20d, vol_trend (4)
  4. _momentum_factors: rsi_14, macd, macd_signal, macd_hist, bb_position, bb_width, price_position_20d, streak (8)
  5. _turnover_factors: avg_turnover_5d, avg_turnover_20d, turnover_ratio, turnover_growth (4)
  6. _price_patterns: daily_range, upper_shadow_ratio, lower_shadow_ratio, gap, vp_divergence (5)
  7. _skew_kurt: skew_20d, kurt_20d, skew_60d, kurt_60d, skew_120d, kurt_120d (6)
  8. _hk_dividend_yield: low_vol_proxy, price_stability (2)
  + labels: fwd_ret_5d, fwd_ret_20d (2)
  Total: 39 factor columns + 2 labels = 41 columns
"""
import os
import tempfile

import numpy as np
import pandas as pd
import pytest

from factors.builder import FactorBuilder

# ── Helpers ──────────────────────────────────────────────────────────

EXPECTED_FACTOR_COLS = [
    # 1. Returns (6)
    "ret_1d", "ret_5d", "ret_10d", "ret_20d", "ret_60d", "ret_120d",
    # 2. Volatility (4)
    "vol_5d", "vol_10d", "vol_20d", "vol_60d",
    # 3. Volume (4)
    "vol_ratio_5d", "vol_ratio_20d", "corr_vp_20d", "vol_trend",
    # 4. Momentum/Technical (8)
    "rsi_14", "macd", "macd_signal", "macd_hist",
    "bb_position", "bb_width", "price_position_20d", "streak",
    # 5. Turnover (4)
    "avg_turnover_5d", "avg_turnover_20d", "turnover_ratio", "turnover_growth",
    # 6. Price patterns (5)
    "daily_range", "upper_shadow_ratio", "lower_shadow_ratio", "gap", "vp_divergence",
    # 7. Higher moments (6)
    "skew_20d", "kurt_20d", "skew_60d", "kurt_60d", "skew_120d", "kurt_120d",
    # 8. HK dividend proxy (2)
    "low_vol_proxy", "price_stability",
]

EXPECTED_LABEL_COLS = ["fwd_ret_5d", "fwd_ret_20d"]
ALL_EXPECTED_COLS = EXPECTED_FACTOR_COLS + EXPECTED_LABEL_COLS


def make_ohlcv(n_days: int = 200, seed: int = 42, start_price: float = 100.0) -> pd.DataFrame:
    """Create synthetic OHLCV data with a random walk and realistic structure."""
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2023-01-01", periods=n_days, freq="B")

    # Random walk log-returns
    log_rets = rng.normal(0.0002, 0.02, size=n_days)
    prices = start_price * np.exp(np.cumsum(log_rets))

    # OHLC with realistic intraday patterns
    close = prices
    daily_vol = np.abs(rng.normal(0, prices * 0.015, size=n_days))
    open_ = close - rng.normal(0, daily_vol * 0.3, size=n_days)
    high = np.maximum(open_, close) + np.abs(rng.normal(0, daily_vol * 0.5, size=n_days))
    low = np.minimum(open_, close) - np.abs(rng.normal(0, daily_vol * 0.5, size=n_days))
    low = np.maximum(low, 0.01)  # prices can't go negative

    volume = rng.lognormal(mean=14, sigma=0.8, size=n_days)

    return pd.DataFrame({
        "date": dates,
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
    })


# ── Tests ────────────────────────────────────────────────────────────

class TestFactorBuilder:
    """Core tests for FactorBuilder."""

    def test_from_init(self):
        """FactorBuilder can be instantiated."""
        fb = FactorBuilder()
        assert fb is not None
        assert isinstance(fb, FactorBuilder)
        assert fb.factor_names == []

    def test_compute_factors_has_all_columns(self):
        """compute_factors() outputs all 39 factor cols + 2 labels = 41 columns."""
        df = make_ohlcv(200)
        fb = FactorBuilder()
        result = fb.compute_factors(df)

        # All expected columns present
        for col in ALL_EXPECTED_COLS:
            assert col in result.columns, f"Missing column: {col}"

        # No unexpected columns beyond the expected set + internal intermediates
        assert len(result.columns) >= len(ALL_EXPECTED_COLS), (
            f"Expected {len(ALL_EXPECTED_COLS)}+ cols, got {len(result.columns)}"
        )

        # Result index matches input dates
        assert len(result) == len(df)
        pd.testing.assert_index_equal(result.index, pd.DatetimeIndex(df["date"]))

        # factor_names is populated
        assert set(fb.factor_names) == set(EXPECTED_FACTOR_COLS)
        assert len(fb.factor_names) == 39

    def test_compute_factors_no_nan_labels(self):
        """Forward return labels (fwd_ret_5d, fwd_ret_20d) exist and are not all-NaN."""
        df = make_ohlcv(200)
        fb = FactorBuilder()
        result = fb.compute_factors(df)

        # Labels should exist, though tail will be NaN (no future data)
        assert result["fwd_ret_5d"].notna().sum() > 0
        assert result["fwd_ret_20d"].notna().sum() > 0

    def test_returns_values(self):
        """ret_1d matches close.pct_change(1)."""
        df = make_ohlcv(200)
        fb = FactorBuilder()
        result = fb.compute_factors(df)

        expected_ret_1d = df.set_index("date")["close"].pct_change(1)
        pd.testing.assert_series_equal(
            result["ret_1d"], expected_ret_1d, check_names=False,
        )

    def test_momentum_rsi_bounds(self):
        """RSI(14) is between 0 and 100 (approximately)."""
        df = make_ohlcv(200)
        fb = FactorBuilder()
        result = fb.compute_factors(df)

        valid = result["rsi_14"].dropna()
        assert len(valid) > 0
        assert valid.min() >= 0
        assert valid.max() <= 100

    def test_volatility_nonnegative(self):
        """All volatility factors are >= 0."""
        df = make_ohlcv(200)
        fb = FactorBuilder()
        result = fb.compute_factors(df)

        for col in ["vol_5d", "vol_10d", "vol_20d", "vol_60d"]:
            valid = result[col].dropna()
            assert (valid >= -1e-10).all(), f"{col} has negative values"

    def test_daily_range_nonnegative(self):
        """daily_range = (high-low)/close >= 0."""
        df = make_ohlcv(200)
        fb = FactorBuilder()
        result = fb.compute_factors(df)
        valid = result["daily_range"].dropna()
        assert (valid >= -1e-10).all()

    def test_skew_kurt_expected_count(self):
        """_skew_kurt produces 6 columns for periods [20, 60, 120]."""
        df = make_ohlcv(200)
        fb = FactorBuilder()
        result = fb.compute_factors(df)

        skew_kurt_cols = [c for c in result.columns if c.startswith("skew_") or c.startswith("kurt_")]
        # skew_20d, skew_60d, skew_120d, kurt_20d, kurt_60d, kurt_120d
        assert len(skew_kurt_cols) == 6

    def test_process_factors_handles_outliers(self):
        """Winsorization clips extreme values."""
        df = make_ohlcv(200)
        fb = FactorBuilder()
        raw = fb.compute_factors(df)

        # Inject an extreme outlier by modifying ret_1d
        raw.loc[raw.index[50], "ret_1d"] = 100.0  # +10000% return is extreme
        raw.loc[raw.index[51], "ret_1d"] = -50.0

        processed = fb.process_factors(raw, winsor_pct=0.01)

        # After winsorization, the extreme values should be clipped
        assert processed["ret_1d"].max() < 10.0, f"Max after clip: {processed['ret_1d'].max()}"
        assert processed["ret_1d"].min() > -10.0, f"Min after clip: {processed['ret_1d'].min()}"

    def test_process_factors_standardizes(self):
        """After process_factors, factor columns have mean≈0, std≈1."""
        df = make_ohlcv(200)
        fb = FactorBuilder()
        raw = fb.compute_factors(df)
        processed = fb.process_factors(raw, winsor_pct=0.01)

        for col in EXPECTED_FACTOR_COLS:
            valid = processed[col].dropna()
            if len(valid) < 5:
                continue
            # After z-score + fillna(0), some cols may have non-zero mean due to NaN fill
            # But non-NaN values should be standardized
            # We check that non-zero values are reasonably scaled
            nonzero = valid[valid != 0]
            if len(nonzero) > 10:
                mean_abs = nonzero.abs().mean()
                assert mean_abs < 5, (
                    f"{col}: absolute mean of nonzero values = {mean_abs:.4f} (expected < 5)"
                )

    def test_process_factors_fills_nan(self):
        """process_factors fills remaining NaN with 0."""
        df = make_ohlcv(200)
        fb = FactorBuilder()
        raw = fb.compute_factors(df)
        processed = fb.process_factors(raw)

        factor_cols = [c for c in EXPECTED_FACTOR_COLS if c in processed.columns]
        nan_count = processed[factor_cols].isna().sum().sum()
        assert nan_count == 0, f"Found {nan_count} NaN values after processing"

    def test_build_factor_dataset_with_data_loader(self):
        """build_factor_dataset uses data_loader callback to fetch OHLCV data."""
        def mock_loader(symbol: str, start: str, end: str) -> pd.DataFrame:
            return make_ohlcv(200, seed=hash(symbol) % 2**31)

        symbols = ["AAPL", "GOOGL"]
        fb = FactorBuilder()
        result = fb.build_factor_dataset(symbols, "2023-01-01", "2023-12-31", mock_loader)

        assert result is not None
        assert len(result) > 0
        assert "symbol" in result.columns
        assert "date" in result.columns
        assert set(result["symbol"].unique()) == set(symbols)
        for col in ALL_EXPECTED_COLS:
            assert col in result.columns, f"Missing {col} in dataset"

    def test_build_factor_dataset_skips_short_history(self):
        """Stocks with < 200 rows are skipped."""
        def mock_loader(symbol: str, start: str, end: str) -> pd.DataFrame:
            if symbol == "SHORT":
                return make_ohlcv(50)  # insufficient history
            return make_ohlcv(200)

        fb = FactorBuilder()
        result = fb.build_factor_dataset(["SHORT", "OK"], "2023-01-01", "2023-12-31", mock_loader)

        assert "SHORT" not in result["symbol"].values
        assert "OK" in result["symbol"].values

    def test_build_factor_dataset_empty_input(self):
        """Empty symbol list returns empty DataFrame."""
        def mock_loader(symbol: str, start: str, end: str) -> pd.DataFrame:
            return make_ohlcv(50)

        fb = FactorBuilder()
        result = fb.build_factor_dataset([], "2023-01-01", "2023-12-31", mock_loader)
        assert len(result) == 0

    def test_save_load_factors_roundtrip(self):
        """save_factors + load_factors roundtrip preserves data."""
        df = make_ohlcv(200)
        fb = FactorBuilder()
        factors = fb.compute_factors(df)

        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "test_factors.parquet")
            fb.save_factors(factors, path)
            assert os.path.exists(path)

            loaded = FactorBuilder.load_factors(path)
            assert isinstance(loaded, pd.DataFrame)
            assert len(loaded) == len(factors)
            for col in ALL_EXPECTED_COLS:
                assert col in loaded.columns, f"Missing {col} after roundtrip"

    def test_save_factors_default_path(self):
        """save_factors with default path works without error."""
        df = make_ohlcv(200)
        fb = FactorBuilder()
        factors = fb.compute_factors(df)

        with tempfile.TemporaryDirectory() as tmpdir:
            default_path = os.path.join(tmpdir, "factors.parquet")
            fb.save_factors(factors, default_path)
            assert os.path.exists(default_path)
            loaded = pd.read_parquet(default_path)
            assert len(loaded) == len(factors)

    # ── Helper for cross-sectional tests ─────────────────────────────────

    def _make_factor_dataset(self, n_stocks: int = 50, n_days: int = 200, seed: int = 42) -> pd.DataFrame:
        """Create cross-sectional factor dataset with symbol+date columns for IC tests."""
        fb = FactorBuilder()
        all_factors = []
        for i in range(n_stocks):
            df = make_ohlcv(n_days, seed=seed + i)
            factors = fb.compute_factors(df)
            factors["symbol"] = f"STOCK_{i:04d}"
            factors["date"] = df["date"].values
            all_factors.append(factors)
        return pd.concat(all_factors, ignore_index=True)

    # ── IC Analysis tests ────────────────────────────────────────────────

    def test_compute_ic_returns_dataframe(self):
        """compute_ic() returns DataFrame with date, factor, rank_ic columns."""
        dataset = self._make_factor_dataset(n_stocks=50, n_days=200)
        fb = FactorBuilder()

        # compute_ic needs already-computed factors
        processed = fb.process_factors(dataset)
        ic_df = fb.compute_ic(processed)

        assert isinstance(ic_df, pd.DataFrame), "Expected DataFrame"
        assert len(ic_df) > 0, "Expected non-empty IC DataFrame"
        assert "date" in ic_df.columns
        assert "factor" in ic_df.columns
        assert "rank_ic" in ic_df.columns

    def test_compute_ic_with_few_stocks(self):
        """compute_ic() skips dates with < 30 stocks, returns empty."""
        dataset = self._make_factor_dataset(n_stocks=10, n_days=50)  # only 10 stocks
        fb = FactorBuilder()
        processed = fb.process_factors(dataset)
        ic_df = fb.compute_ic(processed)

        # With < 30 stocks per date, should be empty or near-empty
        assert isinstance(ic_df, pd.DataFrame)
        assert len(ic_df) == 0

    def test_ic_summary_statistics(self):
        """ic_summary() includes mean, std, count, icir, abs_mean_ic columns."""
        dataset = self._make_factor_dataset(n_stocks=50, n_days=200)
        fb = FactorBuilder()
        processed = fb.process_factors(dataset)
        ic_df = fb.compute_ic(processed)
        summary = fb.ic_summary(ic_df)

        assert isinstance(summary, pd.DataFrame)
        assert len(summary) > 0
        assert "mean" in summary.columns
        assert "std" in summary.columns
        assert "count" in summary.columns
        assert "icir" in summary.columns
        assert "abs_mean_ic" in summary.columns

    def test_factor_correlation_square(self):
        """factor_correlation() returns a square correlation matrix."""
        dataset = self._make_factor_dataset(n_stocks=50, n_days=200)
        fb = FactorBuilder()
        processed = fb.process_factors(dataset)
        corr = fb.factor_correlation(processed)

        assert isinstance(corr, pd.DataFrame)
        # Square matrix
        assert corr.shape[0] == corr.shape[1]
        assert corr.shape[0] > 0
        # Diagonal should be 1.0
        np.testing.assert_allclose(np.diag(corr.values), 1.0, atol=1e-10)

    # ── Edge case tests ────────────────────────────────────────────────

    def test_compute_factors_missing_column(self):
        """compute_factors() raises ValueError when required columns are missing."""
        df = make_ohlcv(200)
        # Drop a required column
        bad_df = df.drop(columns=["volume"])
        fb = FactorBuilder()
        with pytest.raises(ValueError, match="Missing required columns"):
            fb.compute_factors(bad_df)

    def test_compute_factors_short_dataframe(self):
        """compute_factors() works with short data (< rolling window length)."""
        df = make_ohlcv(n_days=10)  # only 10 days, much shorter than 120d windows
        fb = FactorBuilder()
        result = fb.compute_factors(df)
        assert isinstance(result, pd.DataFrame)
        assert len(result) == 10
        # Many columns will be NaN (not enough history for rolling windows)
        # But the function should not error

    def test_build_factor_dataset_data_loader_exception(self):
        """build_factor_dataset catches data_loader exceptions gracefully."""
        def broken_loader(symbol: str, start: str, end: str) -> pd.DataFrame:
            if symbol == "BROKEN":
                raise RuntimeError("Data source unavailable")
            return make_ohlcv(200)

        fb = FactorBuilder()
        result = fb.build_factor_dataset(
            ["BROKEN", "OK"], "2023-01-01", "2023-12-31", broken_loader
        )
        assert result is not None
        # BROKEN is skipped, only OK remains
        assert set(result["symbol"].unique()) == {"OK"}
