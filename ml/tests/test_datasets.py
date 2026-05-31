"""Tests for DatasetManager."""
import pytest
import pandas as pd
from google.cloud import bigquery
from ml.datasets import DatasetManager, DatasetConfig, DatasetBundle


def test_dataset_create_and_load():
    """Create a minimal dataset, verify meta + load."""
    config = DatasetConfig(
        market="us",
        symbols=["AAPL", "MSFT"],
        features=["mom_20d", "vol_20d"],
        label="fwd_ret_5d",
        train_range=("2025-01-01", "2025-03-31"),
        val_range=("2025-04-01", "2025-04-30"),
        test_range=("2025-05-01", "2025-05-31"),
    )
    name = "test_mini_v1"
    
    DatasetManager.create(name, config)
    
    assert DatasetManager.exists(name)
    
    bundle = DatasetManager.load(name)
    assert isinstance(bundle, DatasetBundle)
    assert bundle.name == name
    assert bundle.meta["market"] == "us"
    assert bundle.meta["n_symbols"] >= 1
    assert "train_range" in bundle.meta
    assert "val_range" in bundle.meta
    assert "test_range" in bundle.meta
    assert len(bundle.train) > 0
    assert len(bundle.val) > 0
    assert len(bundle.test) > 0
    assert "mom_20d" in bundle.train.columns
    assert "fwd_ret_5d" in bundle.train.columns


def test_dataset_list_all():
    """List should include the created dataset."""
    datasets = DatasetManager.list_all()
    names = [d["name"] for d in datasets]
    assert any("test_mini" in n for n in names)


def test_exists_false():
    """exists() returns False for a nonexistent dataset."""
    assert DatasetManager.exists("nonexistent_dataset_xyz_123") is False


def test_from_registry_top_n():
    """features="from_registry_top_3" resolves to real factor IDs, not characters."""
    # Get expected top-3 features from factor_evaluations (ground truth)
    bq = bigquery.Client(project=DatasetManager.DEFAULT_PROJECT)
    top_q = f"""
        WITH latest AS (
            SELECT factor_id, ic_mean,
                   ROW_NUMBER() OVER (PARTITION BY factor_id ORDER BY evaluated_at DESC) AS rn
            FROM `{DatasetManager.DEFAULT_PROJECT}.quant.factor_evaluations`
        )
        SELECT factor_id FROM latest WHERE rn = 1 ORDER BY ABS(ic_mean) DESC LIMIT 3
    """
    df = bq.query(top_q).to_dataframe()
    expected_ids = df["factor_id"].tolist()
    # Strip "us_" prefix for expected output column names
    expected_features = [
        fid[3:] if fid.startswith("us_") else fid for fid in expected_ids
    ]

    config = DatasetConfig(
        market="us",
        symbols=["AAPL"],
        features="from_registry_top_3",
        label="test_factor",
        train_range=("2025-01-01", "2025-03-31"),
        val_range=("2025-04-01", "2025-04-30"),
        test_range=("2025-05-01", "2025-05-31"),
    )
    name = "test_registry_top3_v1"

    DatasetManager.create(name, config)

    assert DatasetManager.exists(name)
    bundle = DatasetManager.load(name)

    # Verify features are real strings, not single characters
    actual_features = bundle.meta["features"]
    assert len(actual_features) == 3, f"Expected 3 features, got {actual_features}"
    for f in actual_features:
        assert len(f) > 1, f"Feature '{f}' looks like a broken single-char name"

    # Verify features match factor_evaluations top-3 (after stripping us_ prefix)
    assert actual_features == expected_features, (
        f"Features mismatch: {actual_features} != {expected_features}"
    )

    # Verify feature columns exist in train DataFrame
    for f in actual_features:
        assert f in bundle.train.columns, f"Missing feature column '{f}' in train data"
