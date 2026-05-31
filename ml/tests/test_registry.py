"""Tests for ModelRegistry."""
import pytest
import numpy as np
import lightgbm as lgb
from ml.registry import ModelRegistry, ModelBundle


def _make_dummy_model():
    X = np.random.randn(100, 3)
    y = np.random.randn(100) * 0.1 + X[:, 0] * 0.5
    model = lgb.train(
        {"objective": "regression", "verbose": -1},
        lgb.Dataset(X, y),
        num_boost_round=10,
    )
    return model


def test_save_and_load():
    """Save a dummy model, load it back."""
    model = _make_dummy_model()
    config = {
        "model": {"name": "test_lgbm", "type": "lightgbm"},
        "factors": {"source": "registry", "top_n": 5, "exclude": []},
        "data": {"dataset": "test_dummy", "label": "fwd_ret_5d"},
    }
    metrics = {"val_ic": 0.05, "val_rmse": 0.02, "train_rmse": 0.01}
    features = ["feat_a", "feat_b", "feat_c"]

    version = ModelRegistry.save(
        name="test_lgbm",
        model=model,
        config=config,
        metrics=metrics,
        features=features,
        dataset_name="test_dummy",
    )
    assert isinstance(version, int)
    assert version > 0

    bundle = ModelRegistry.load("test_lgbm", version=version)
    assert isinstance(bundle, ModelBundle)
    assert bundle.name == "test_lgbm"
    assert bundle.version == version
    assert bundle.features == features
    assert isinstance(bundle.model, lgb.Booster)


def test_list_versions():
    """Should list all versions of a model."""
    versions = ModelRegistry.list_versions("test_lgbm")
    assert len(versions) >= 1
    assert "version" in versions[0]
    assert "metrics" in versions[0]


def test_promote():
    """Should transition stage."""
    latest = ModelRegistry.list_versions("test_lgbm")[0]["version"]
    ModelRegistry.promote("test_lgbm", latest, "staging")
    versions = ModelRegistry.list_versions("test_lgbm")
    staging_versions = [v for v in versions if v.get("stage") == "staging"]
    assert len(staging_versions) >= 1
