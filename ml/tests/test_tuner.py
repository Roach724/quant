"""Tests for OptunaTuner."""
import numpy as np
import tempfile
from pathlib import Path
import yaml
import pandas as pd
from ml.tuner import OptunaTuner
from ml.datasets import DatasetBundle


def test_tuner_runs_and_returns_bundle():
    """Tuner should complete trials and return ModelBundle."""
    config = {
        "model": {"name": "test_optuna", "type": "lightgbm"},
        "factors": {"source": "registry", "top_n": 3, "exclude": []},
        "hyperparams": {
            "search_space": {
                "num_leaves": {"type": "int", "low": 7, "high": 31},
                "learning_rate": {"type": "loguniform", "low": 0.01, "high": 0.1},
            },
            "fixed": {
                "objective": "regression",
                "metric": "rmse",
                "verbose": -1,
                "early_stopping_rounds": 10,
                "random_state": 42,
            },
        },
        "data": {"dataset": None, "label": "fwd_ret_5d"},
        "training": {
            "optuna_direction": "minimize",
            "optuna_metric": "val_rmse",
            "n_trials": 3,
        },
    }

    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        yaml.dump(config, f)
        config_path = f.name

    np.random.seed(42)
    n = 200
    features = np.random.randn(n, 5)
    feature_cols = [f"feat_{i}" for i in range(5)]
    true_coef = np.array([0.3, -0.1, 0.05, 0.0, 0.0])
    y = features @ true_coef + np.random.randn(n) * 0.05

    df = pd.DataFrame(features, columns=feature_cols)
    df["fwd_ret_5d"] = y
    df["date"] = pd.date_range("2025-01-01", periods=n)

    train = df.iloc[:100]
    val = df.iloc[100:160]
    test = df.iloc[160:]

    dummy_bundle = DatasetBundle(
        name="test_dummy_data",
        meta={"features": feature_cols},
        train=train, val=val, test=test,
    )

    tuner = OptunaTuner(config_path)
    bundle = tuner._tune_with_data(dummy_bundle, n_trials=3)

    assert bundle.name == "test_optuna"
    assert bundle.version > 0
    assert len(bundle.features) > 0

    Path(config_path).unlink()
