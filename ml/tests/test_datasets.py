"""Tests for DatasetManager."""
import pytest
import pandas as pd
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
