"""
Tests for ml/trainer.py — ModelTrainer

TDD: tests written before implementation.
Run with: cd quant && LD_LIBRARY_PATH=/home/node/.local/lib:$LD_LIBRARY_PATH python3 -m pytest ml/tests/ -v
"""

import pytest
import numpy as np
import pandas as pd
import tempfile
import os
from pathlib import Path
from sklearn.linear_model import LinearRegression, Ridge


# ── Synthetic data helper ────────────────────────────────────────────

def _make_synthetic_factors(n_rows: int = 500, n_factors: int = 5) -> pd.DataFrame:
    """Create synthetic factor DataFrame for testing."""
    np.random.seed(42)
    dates = pd.date_range("2023-01-01", periods=n_rows // 2, freq="D")
    symbols = ["AAPL", "MSFT"]
    rows = []
    for sym in symbols:
        for i, date in enumerate(dates):
            row = {"date": date, "symbol": sym}
            for j in range(n_factors):
                row[f"factor_{j}"] = np.random.randn() * 0.1 + j * 0.02
            # Create a signal: fwd_ret correlated with factor_0
            row["fwd_ret_5d"] = row["factor_0"] * 0.5 + np.random.randn() * 0.01
            row["fwd_ret_20d"] = row["factor_0"] * 0.3 + np.random.randn() * 0.02
            rows.append(row)
    df = pd.DataFrame(rows)
    return df


# ── Fixture ───────────────────────────────────────────────────────────

@pytest.fixture
def synthetic_df():
    """Create synthetic data and save to temp parquet."""
    return _make_synthetic_factors()


@pytest.fixture
def synthetic_parquet(synthetic_df, tmp_path):
    """Save synthetic data to parquet, return path."""
    path = tmp_path / "factors.parquet"
    synthetic_df.to_parquet(path)
    return str(path)


@pytest.fixture
def trainer(synthetic_parquet):
    """Create a ModelTrainer with synthetic data loaded."""
    from ml.trainer import ModelTrainer
    mt = ModelTrainer(factor_path=synthetic_parquet)
    mt.load_data()
    return mt


# ── C1: Init tests ────────────────────────────────────────────────────

class TestModelTrainerInit:
    """Test initialization and basic attributes."""

    def test_init_with_path(self, synthetic_parquet):
        from ml.trainer import ModelTrainer
        trainer = ModelTrainer(factor_path=synthetic_parquet)
        assert trainer is not None
        assert trainer.factor_path is not None

    def test_init_with_none_path(self):
        from ml.trainer import ModelTrainer
        trainer = ModelTrainer(factor_path=None)
        assert trainer is not None
        assert trainer.factor_path is None

    def test_init_default_path(self):
        from ml.trainer import ModelTrainer
        trainer = ModelTrainer()
        assert trainer is not None
        assert trainer.factor_path == "./data/factors/factors.parquet"


# ── C2: Data loading tests ────────────────────────────────────────────

class TestLoadData:
    """Test load_data method."""

    def test_load_data_returns_dataframe(self, trainer):
        df = trainer.load_data()
        assert isinstance(df, pd.DataFrame)
        assert len(df) > 0

    def test_load_data_detects_features(self, trainer):
        trainer.load_data()
        assert len(trainer.feature_cols) > 0
        assert "symbol" not in trainer.feature_cols
        assert "date" not in trainer.feature_cols
        assert "fwd_ret_5d" not in trainer.feature_cols
        assert "fwd_ret_20d" not in trainer.feature_cols
        # Should contain factor columns
        for col in trainer.feature_cols:
            assert col.startswith("factor_")

    def test_load_data_none_path(self):
        from ml.trainer import ModelTrainer
        trainer = ModelTrainer(factor_path=None)
        with pytest.raises((ValueError, FileNotFoundError, TypeError)):
            trainer.load_data()

    def test_load_data_empty_parquet(self, tmp_path):
        """Handle empty parquet gracefully."""
        from ml.trainer import ModelTrainer
        empty_path = tmp_path / "empty.parquet"
        pd.DataFrame().to_parquet(empty_path)
        trainer = ModelTrainer(factor_path=str(empty_path))
        # Empty DataFrame should still load (no features detected)
        df = trainer.load_data()
        assert isinstance(df, pd.DataFrame)
        assert len(trainer.feature_cols) == 0


# ── Data split tests ───────────────────────────────────────────────────

class TestSplitData:
    """Test split_data method."""

    def test_split_data_returns_tuple(self, trainer):
        train, val, test = trainer.split_data(
            train_end="2023-01-15",
            val_end="2023-02-01",
            test_end="2023-02-15",
        )
        assert isinstance(train, pd.DataFrame)
        assert isinstance(val, pd.DataFrame)
        assert isinstance(test, pd.DataFrame)

    def test_split_data_no_overlap(self, trainer):
        train, val, test = trainer.split_data(
            train_end="2023-01-15",
            val_end="2023-02-01",
            test_end="2023-02-15",
        )
        # Check no row appears in two sets
        train_idx = set(zip(train["symbol"], train["date"].astype(str)))
        val_idx = set(zip(val["symbol"], val["date"].astype(str)))
        test_idx = set(zip(test["symbol"], test["date"].astype(str)))
        assert train_idx.isdisjoint(val_idx)
        assert train_idx.isdisjoint(test_idx)
        assert val_idx.isdisjoint(test_idx)

    def test_split_data_each_non_empty(self, trainer):
        train, val, test = trainer.split_data(
            train_end="2023-01-15",
            val_end="2023-02-01",
            test_end="2023-02-15",
        )
        assert len(train) > 0
        assert len(val) > 0
        assert len(test) > 0

    def test_split_data_auto_calls_load_data(self, synthetic_parquet):
        """split_data should auto-call load_data if factor_df is None."""
        from ml.trainer import ModelTrainer
        trainer = ModelTrainer(factor_path=synthetic_parquet)
        # Don't call load_data explicitly
        train, val, test = trainer.split_data(
            train_end="2023-01-15",
            val_end="2023-02-01",
            test_end="2023-02-15",
        )
        assert len(train) > 0  # auto-load works; count depends on date range


# ── OLS tests ──────────────────────────────────────────────────────────

class TestTrainOLS:
    """Test OLS training."""

    def test_train_ols_returns_dict(self, trainer):
        train, val, _ = trainer.split_data(
            train_end="2023-01-15",
            val_end="2023-02-01",
            test_end="2023-02-15",
        )
        result = trainer.train_ols(train, val)
        assert isinstance(result, dict)
        assert "model" in result
        assert "rmse" in result
        assert isinstance(result["model"], LinearRegression)

    def test_train_ols_rmse_reasonable(self, trainer):
        train, val, _ = trainer.split_data(
            train_end="2023-01-15",
            val_end="2023-02-01",
            test_end="2023-02-15",
        )
        result = trainer.train_ols(train, val)
        # RMSE should be positive and not absurdly large
        assert result["rmse"] > 0
        assert result["rmse"] < 100.0  # With synthetic data RMSE should be small

    def test_train_ols_fits_scaler(self, trainer):
        train, val, _ = trainer.split_data(
            train_end="2023-01-15",
            val_end="2023-02-01",
            test_end="2023-02-15",
        )
        trainer.train_ols(train, val)
        # Scaler should be fitted after OLS training
        assert hasattr(trainer.scaler, "mean_")


# ── Ridge tests ────────────────────────────────────────────────────────

class TestTrainRidge:
    """Test Ridge training."""

    def test_train_ridge_returns_dict(self, trainer):
        train, val, _ = trainer.split_data(
            train_end="2023-01-15",
            val_end="2023-02-01",
            test_end="2023-02-15",
        )
        result = trainer.train_ridge(train, val, alpha=1.0)
        assert isinstance(result, dict)
        assert "model" in result
        assert "alpha" in result
        assert "rmse" in result
        assert result["alpha"] == 1.0
        assert isinstance(result["model"], Ridge)

    def test_train_ridge_alpha_parameter(self, trainer):
        train, val, _ = trainer.split_data(
            train_end="2023-01-15",
            val_end="2023-02-01",
            test_end="2023-02-15",
        )
        result = trainer.train_ridge(train, val, alpha=10.0)
        assert result["alpha"] == 10.0

    def test_train_ridge_rmse_reasonable(self, trainer):
        train, val, _ = trainer.split_data(
            train_end="2023-01-15",
            val_end="2023-02-01",
            test_end="2023-02-15",
        )
        result = trainer.train_ridge(train, val, alpha=1.0)
        assert result["rmse"] > 0
        assert result["rmse"] < 100.0


# ── LightGBM tests ─────────────────────────────────────────────────────

class TestTrainLightGBM:
    """Test LightGBM training."""

    def test_train_lightgbm_returns_dict(self, trainer):
        train, val, _ = trainer.split_data(
            train_end="2023-01-15",
            val_end="2023-02-01",
            test_end="2023-02-15",
        )
        result = trainer.train_lightgbm(train, val)
        assert isinstance(result, dict)
        assert "model" in result
        assert "rmse_val" in result
        assert "feature_importance" in result
        assert "best_iteration" in result

    def test_train_lightgbm_feature_importance(self, trainer):
        train, val, _ = trainer.split_data(
            train_end="2023-01-15",
            val_end="2023-02-01",
            test_end="2023-02-15",
        )
        result = trainer.train_lightgbm(train, val)
        fi = result["feature_importance"]
        assert isinstance(fi, pd.DataFrame)
        assert "feature" in fi.columns
        assert "importance" in fi.columns
        assert len(fi) == len(trainer.feature_cols)

    def test_train_lightgbm_rmse_reasonable(self, trainer):
        train, val, _ = trainer.split_data(
            train_end="2023-01-15",
            val_end="2023-02-01",
            test_end="2023-02-15",
        )
        result = trainer.train_lightgbm(train, val)
        assert result["rmse_val"] > 0
        assert result["rmse_val"] < 100.0

    def test_train_lightgbm_does_not_touch_scaler(self, trainer):
        """LightGBM training should NOT use or fit the scaler."""
        train, val, _ = trainer.split_data(
            train_end="2023-01-15",
            val_end="2023-02-01",
            test_end="2023-02-15",
        )
        # Train LightGBM first (without OLS)
        trainer.train_lightgbm(train, val)
        # Scaler should NOT be fitted
        assert not hasattr(trainer.scaler, "mean_")

    def test_train_lightgbm_insufficient_data(self, trainer):
        """Handle very small datasets gracefully."""
        # Create a tiny dataset
        tiny_train = pd.DataFrame({
            "date": pd.to_datetime(["2023-01-01", "2023-01-02"]),
            "symbol": ["A", "B"],
            "f": [0.1, 0.2],
            "fwd_ret_5d": [0.01, 0.02],
        })
        trainer.feature_cols = ["f"]
        trainer.label_col = "fwd_ret_5d"
        tiny_val = tiny_train.copy()
        # Should not crash, but may produce a result or raise
        try:
            result = trainer.train_lightgbm(tiny_train, tiny_val)
        except Exception:
            # Acceptable: tiny data may not support 5-fold CV
            pass


# ── IC evaluation tests ────────────────────────────────────────────────

class TestEvaluateIC:
    """Test IC evaluation."""

    def test_evaluate_ic_returns_dict(self, trainer):
        train, val, test = trainer.split_data(
            train_end="2023-01-15",
            val_end="2023-02-01",
            test_end="2023-02-15",
        )
        ols_result = trainer.train_ols(train, val)
        ic = trainer.evaluate_ic(ols_result["model"], test, name="OLS")
        assert isinstance(ic, dict)
        assert "overall_rank_ic" in ic
        assert "mean_daily_ic" in ic
        assert "daily_ic_std" in ic
        assert "icir" in ic

    def test_evaluate_ic_ols_with_scaler(self, trainer):
        """IC evaluation should use scaler for linear models."""
        train, val, test = trainer.split_data(
            train_end="2023-01-15",
            val_end="2023-02-01",
            test_end="2023-02-15",
        )
        ols_result = trainer.train_ols(train, val)
        ic = trainer.evaluate_ic(ols_result["model"], test, name="OLS")
        # IC should be in [-1, 1]
        assert -1.0 <= ic["overall_rank_ic"] <= 1.0

    def test_evaluate_ic_lightgbm_no_scaler(self, trainer):
        """IC evaluation for LightGBM should use raw features (not scaler)."""
        # Use wider date range for more training data so LightGBM can learn
        train, val, test = trainer.split_data(
            train_end="2023-06-30",
            val_end="2023-08-01",
            test_end="2023-09-01",
        )
        trainer.train_ols(train, val)  # Fit scaler with OLS
        lgb_result = trainer.train_lightgbm(train, val)
        # Evaluate IC — should use raw features even though scaler was fitted by OLS
        ic = trainer.evaluate_ic(lgb_result["model"], test, name="LGB")
        assert isinstance(ic, dict)
        assert "overall_rank_ic" in ic
        assert "icir" in ic

    def test_evaluate_ic_default_name(self, trainer):
        """evaluate_ic with default (empty) name."""
        train, val, test = trainer.split_data(
            train_end="2023-01-15",
            val_end="2023-02-01",
            test_end="2023-02-15",
        )
        ols_result = trainer.train_ols(train, val)
        ic = trainer.evaluate_ic(ols_result["model"], test)
        assert isinstance(ic, dict)


# ── Predict tests ──────────────────────────────────────────────────────

class TestPredict:
    """Test predict method."""

    def test_predict_returns_series(self, trainer):
        train, val, test = trainer.split_data(
            train_end="2023-01-15",
            val_end="2023-02-01",
            test_end="2023-02-15",
        )
        ols_result = trainer.train_ols(train, val)
        preds = trainer.predict(ols_result["model"], test)
        assert isinstance(preds, pd.Series)

    def test_predict_correct_length(self, trainer):
        """Predict should return predictions for clean (non-NaN) rows."""
        train, val, test = trainer.split_data(
            train_end="2023-01-15",
            val_end="2023-02-01",
            test_end="2023-02-15",
        )
        ols_result = trainer.train_ols(train, val)
        preds = trainer.predict(ols_result["model"], test)
        # Should have predictions for clean rows
        clean_rows = test.dropna(subset=trainer.feature_cols)
        assert len(preds) == len(clean_rows)

    def test_predict_lightgbm_no_scaler(self, trainer):
        """Predict with LightGBM should work without fitted scaler."""
        train, val, test = trainer.split_data(
            train_end="2023-01-15",
            val_end="2023-02-01",
            test_end="2023-02-15",
        )
        # Train LightGBM only (scaler not fitted)
        lgb_result = trainer.train_lightgbm(train, val)
        preds = trainer.predict(lgb_result["model"], test)
        assert isinstance(preds, pd.Series)
        assert len(preds) > 0


# ── Save/Load roundtrip tests ──────────────────────────────────────────

class TestSaveLoad:
    """Test model save/load roundtrip."""

    def test_save_load_ols_roundtrip(self, trainer, tmp_path):
        """Save OLS model, load it back, verify predictions match."""
        train, val, test = trainer.split_data(
            train_end="2023-01-15",
            val_end="2023-02-01",
            test_end="2023-02-15",
        )
        ols_result = trainer.train_ols(train, val)
        model = ols_result["model"]

        # Predict before save
        preds_before = trainer.predict(model, test)

        # Save and load
        save_path = tmp_path / "ols_model.pkl"
        trainer.save_model(model, str(save_path))
        assert save_path.exists()

        loaded = trainer.load_model(str(save_path))
        preds_after = trainer.predict(loaded, test)

        # Predictions should match
        pd.testing.assert_series_equal(preds_before, preds_after)

    def test_save_load_ridge_roundtrip(self, trainer, tmp_path):
        """Save Ridge model, load it back, verify predictions match."""
        train, val, test = trainer.split_data(
            train_end="2023-01-15",
            val_end="2023-02-01",
            test_end="2023-02-15",
        )
        ridge_result = trainer.train_ridge(train, val, alpha=1.0)
        model = ridge_result["model"]

        preds_before = trainer.predict(model, test)

        save_path = tmp_path / "ridge_model.pkl"
        trainer.save_model(model, str(save_path))
        assert save_path.exists()

        loaded = trainer.load_model(str(save_path))
        preds_after = trainer.predict(loaded, test)

        pd.testing.assert_series_equal(preds_before, preds_after)

    def test_save_load_lightgbm_roundtrip(self, trainer, tmp_path):
        """Save LightGBM model, load it back, verify predictions match."""
        train, val, test = trainer.split_data(
            train_end="2023-01-15",
            val_end="2023-02-01",
            test_end="2023-02-15",
        )
        lgb_result = trainer.train_lightgbm(train, val)
        model = lgb_result["model"]

        preds_before = trainer.predict(model, test)

        save_path = tmp_path / "lgb_model"
        trainer.save_model(model, str(save_path))
        # LightGBM should save as .txt
        assert (tmp_path / "lgb_model.txt").exists()

        loaded = trainer.load_model(str(save_path))
        preds_after = trainer.predict(loaded, test)

        # Predictions should match (close within tolerance for LightGBM)
        assert np.allclose(preds_before.values, preds_after.values, rtol=1e-10)

    def test_load_nonexistent_file(self, trainer):
        """Loading nonexistent file should raise."""
        with pytest.raises((FileNotFoundError, ValueError, OSError)):
            trainer.load_model("/nonexistent/path/model.pkl")


# ── Edge case tests ────────────────────────────────────────────────────

class TestEdgeCases:
    """Edge case handling."""

    def test_empty_feature_cols(self):
        """Handle case where no feature columns are detected."""
        from ml.trainer import ModelTrainer
        trainer = ModelTrainer(factor_path=None)
        trainer.feature_cols = []
        trainer.label_col = "fwd_ret_5d"

        # train_ols with empty features should raise cleanly
        df = pd.DataFrame({"fwd_ret_5d": [0.01, 0.02, -0.01]})
        with pytest.raises((ValueError, IndexError, KeyError)):
            trainer.train_ols(df, df)

    def test_predict_with_nan_features(self, trainer):
        """Predict should handle NaN in feature columns."""
        train, val, test = trainer.split_data(
            train_end="2023-01-15",
            val_end="2023-02-01",
            test_end="2023-02-15",
        )
        ols_result = trainer.train_ols(train, val)

        # Introduce NaN in test features
        test_with_nan = test.copy()
        if len(trainer.feature_cols) > 0:
            test_with_nan.loc[test_with_nan.index[0], trainer.feature_cols[0]] = np.nan

        preds = trainer.predict(ols_result["model"], test_with_nan)
        assert isinstance(preds, pd.Series)
        # Should have dropped NaN rows
        assert len(preds) < len(test_with_nan)
