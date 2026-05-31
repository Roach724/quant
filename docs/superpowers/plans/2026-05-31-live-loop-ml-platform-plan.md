# Live Loop & ML Platform — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a unified Live Loop runner (paper + real trading) with observable state and post-run reports, plus an ML platform with MLflow model registry, Optuna hyperparameter tuning, and versioned training datasets.

**Architecture:** Three packages — `live/` (LiveRunner + Observer + Reporter), `ml/` (ModelRegistry + OptunaTuner + DatasetManager + existing trainer), `dashboard/` (WebSocket extension). LiveRunner consumes models via `ModelRegistry.load()`, all broker interaction through existing Broker protocol.

**Tech Stack:** Python 3.12, MLflow, Optuna, LightGBM, FastAPI + WebSocket, matplotlib, Google BigQuery, GCS Parquet

---

## Prerequisites

- [ ] **Step 0: Install dependencies**

```bash
pip install mlflow optuna fastapi uvicorn websockets
```

Run: `python3.12 -c "import mlflow, optuna, fastapi, uvicorn; print('OK')"`
Expected: `OK`

---

## File Structure

```
NEW:
  ml/datasets.py                  # DatasetManager: create/load/list GCS datasets
  ml/registry.py                  # ModelRegistry: MLflow save/load/list/promote
  ml/tuner.py                     # OptunaTuner: config-driven hyperparameter tuning
  models/momentum_lgbm/v1/config.yaml  # Initial model configuration
  live/__init__.py                # Package init
  live/runner.py                  # LiveRunner main loop
  live/observer.py                # Observer: logs, snapshots, trade records
  live/reporter.py                # Reporter: HTML report + charts
  live/config.py                  # Config loading + validation
  live/run.py                     # CLI entry point
  live/configs/paper_us.yaml      # Paper trading config (US)
  live/configs/live_us.yaml       # Live trading config (US, Futu)

MODIFY:
  strategies/ml_pred.py           # _train() → _load_model() with fallback
  dashboard/api.py                # Add WebSocket endpoint
  ml/__init__.py                  # Export new classes

UNCHANGED:
  ml/trainer.py, run_paper.py, collectors/*, bigquery_loader/*, factor pipeline, cron
```

---

## Phase 1: ML Infrastructure

### Task 1.1: DatasetManager — create datasets from BQ → GCS Parquet

**Files:**
- Create: `ml/datasets.py`
- Create: `ml/tests/test_datasets.py`

- [ ] **Step 1: Write test — create and load a dataset**

Create `ml/tests/test_datasets.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /opt/quant && python3.12 -m pytest ml/tests/test_datasets.py -v
```

Expected: `FAILED - ModuleNotFoundError: No module named 'ml.datasets'`

- [ ] **Step 3: Implement DatasetManager**

Create `ml/datasets.py`:

```python
"""DatasetManager — versioned training datasets stored as GCS Parquet.

Each dataset = {train, val, test}.parquet + meta.json under gs://bucket/datasets/{name}/.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Union

import pandas as pd
from google.cloud import bigquery, storage

logger = logging.getLogger(__name__)

DEFAULT_BUCKET = "deductive-notch-495015-c2-quant-data"
DEFAULT_PROJECT = "deductive-notch-495015-c2"
DATASETS_PREFIX = "datasets"


@dataclass
class DatasetConfig:
    market: str
    symbols: Union[list[str], str]     # list of symbols or "all"
    features: Union[list[str], str]    # explicit list or "from_registry_top_N"
    label: str
    train_range: tuple[str, str]
    val_range: tuple[str, str]
    test_range: tuple[str, str]


@dataclass
class DatasetBundle:
    name: str
    meta: dict
    train: pd.DataFrame
    val: pd.DataFrame
    test: pd.DataFrame


class DatasetManager:
    """Create, load, and list versioned training datasets on GCS."""

    @classmethod
    def create(cls, name: str, config: DatasetConfig, bucket: str = DEFAULT_BUCKET) -> str:
        """Query BQ factor_values → split by date → write GCS Parquet.
        
        Returns the dataset name.
        """
        bq_client = bigquery.Client(project=DEFAULT_PROJECT)
        gcs_client = storage.Client(project=DEFAULT_PROJECT)

        # Resolve symbols
        if config.symbols == "all" or (isinstance(config.symbols, list) and len(config.symbols) == 0):
            symbols = cls._all_symbols(bq_client, config.market)
        else:
            symbols = config.symbols

        # Resolve features
        if isinstance(config.features, str) and config.features.startswith("from_registry_top_"):
            n = int(config.features.split("_")[-1])
            features = cls._top_features_from_registry(bq_client, n, exclude=["fwd_ret_5d", "fwd_ret_20d"])
        else:
            features = config.features

        sym_list = ", ".join(f"'{s}'" for s in symbols)
        feat_list = ", ".join(features)

        def _load_range(start: str, end: str) -> pd.DataFrame:
            query = f"""
                SELECT symbol, date, {feat_list}, {config.label}
                FROM quant.factor_values
                WHERE symbol IN ({sym_list})
                  AND date BETWEEN '{start}' AND '{end}'
                ORDER BY symbol, date
            """
            return bq_client.query(query).result().to_dataframe()

        train_df = _load_range(*config.train_range)
        val_df = _load_range(*config.val_range)
        test_df = _load_range(*config.test_range)

        # Drop rows with NaN in features
        for df in [train_df, val_df, test_df]:
            df.dropna(subset=features + [config.label], inplace=True)

        bucket_obj = gcs_client.bucket(bucket)
        base = f"{DATASETS_PREFIX}/{name}"

        for df, fname in [(train_df, "train.parquet"), (val_df, "val.parquet"), (test_df, "test.parquet")]:
            path = Path(f"/tmp/{name}_{fname}")
            df.to_parquet(path, index=False)
            blob = bucket_obj.blob(f"{base}/{fname}")
            blob.upload_from_filename(str(path))
            path.unlink()

        # Write meta.json
        import subprocess
        try:
            git_commit = subprocess.check_output(
                ["git", "rev-parse", "--short", "HEAD"], text=True
            ).strip()
        except Exception:
            git_commit = "unknown"

        from datetime import datetime, timezone
        meta = {
            "name": name,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "market": config.market,
            "symbols": symbols,
            "n_symbols": len(symbols),
            "features": features,
            "n_features": len(features),
            "label": config.label,
            "train_range": list(config.train_range),
            "val_range": list(config.val_range),
            "test_range": list(config.test_range),
            "train_rows": len(train_df),
            "val_rows": len(val_df),
            "test_rows": len(test_df),
            "factor_computed_at": datetime.now(timezone.utc).isoformat(),
            "git_commit": git_commit,
        }
        meta_path = Path(f"/tmp/{name}_meta.json")
        meta_path.write_text(json.dumps(meta, indent=2))
        blob = bucket_obj.blob(f"{base}/meta.json")
        blob.upload_from_filename(str(meta_path))
        meta_path.unlink()

        logger.info("Dataset %s created: train=%d val=%d test=%d (%d features)",
                     name, len(train_df), len(val_df), len(test_df), len(features))
        return name

    @classmethod
    def load(cls, name: str, bucket: str = DEFAULT_BUCKET) -> DatasetBundle:
        """Load train/val/test + meta from GCS."""
        gcs_client = storage.Client(project=DEFAULT_PROJECT)
        bucket_obj = gcs_client.bucket(bucket)
        base = f"{DATASETS_PREFIX}/{name}"

        # Load meta
        meta_blob = bucket_obj.blob(f"{base}/meta.json")
        meta = json.loads(meta_blob.download_as_text())

        tmp = Path(f"/tmp/{name}")
        tmp.mkdir(exist_ok=True)

        train = cls._download_parquet(bucket_obj, f"{base}/train.parquet", tmp / "train.parquet")
        val = cls._download_parquet(bucket_obj, f"{base}/val.parquet", tmp / "val.parquet")
        test = cls._download_parquet(bucket_obj, f"{base}/test.parquet", tmp / "test.parquet")

        for f in tmp.iterdir():
            f.unlink()
        tmp.rmdir()

        return DatasetBundle(name=name, meta=meta, train=train, val=val, test=test)

    @classmethod
    def list_all(cls, bucket: str = DEFAULT_BUCKET) -> list[dict]:
        """List all datasets with metadata summaries."""
        gcs_client = storage.Client(project=DEFAULT_PROJECT)
        bucket_obj = gcs_client.bucket(bucket)
        blobs = list(bucket_obj.list_blobs(prefix=f"{DATASETS_PREFIX}/", delimiter="/"))
        
        results = []
        seen = set()
        for b in blobs:
            # Each dataset folder: prefix looks like "datasets/name/"
            parts = b.name.split("/")
            if len(parts) >= 2 and parts[0] == DATASETS_PREFIX:
                ds_name = parts[1]
                if ds_name and ds_name not in seen:
                    seen.add(ds_name)
                    try:
                        meta_blob = bucket_obj.blob(f"{DATASETS_PREFIX}/{ds_name}/meta.json")
                        if meta_blob.exists():
                            meta = json.loads(meta_blob.download_as_text())
                            results.append(meta)
                    except Exception:
                        pass
        return results

    @classmethod
    def exists(cls, name: str, bucket: str = DEFAULT_BUCKET) -> bool:
        """Check if dataset exists in GCS."""
        gcs_client = storage.Client(project=DEFAULT_PROJECT)
        bucket_obj = gcs_client.bucket(bucket)
        blob = bucket_obj.blob(f"{DATASETS_PREFIX}/{name}/meta.json")
        return blob.exists()

    # ── helpers ──

    @staticmethod
    def _all_symbols(bq_client, market: str) -> list[str]:
        df = bq_client.query(f"""
            SELECT DISTINCT symbol FROM quant.factor_values
            WHERE symbol LIKE '{market.upper()}.%'
        """).result().to_dataframe()
        if df.empty:
            table = "us_bars_1d" if market == "us" else (
                "hk_bars_1d" if market == "hk" else "crypto_bars_1d"
            )
            df = bq_client.query(f"SELECT DISTINCT symbol FROM quant.{table}").result().to_dataframe()
        return sorted(df["symbol"].tolist())

    @staticmethod
    def _top_features_from_registry(bq_client, n: int, exclude: list[str]) -> list[str]:
        df = bq_client.query("""
            SELECT factor_id, AVG(ABS(info_coefficient)) as abs_ic
            FROM quant.factor_evaluations
            WHERE info_coefficient IS NOT NULL
            GROUP BY factor_id
            ORDER BY abs_ic DESC
            LIMIT {}
        """.format(n)).result().to_dataframe()
        features = [f for f in df["factor_id"].tolist() if f not in exclude]
        return features

    @staticmethod
    def _download_parquet(bucket_obj, blob_path: str, local_path: Path) -> pd.DataFrame:
        blob = bucket_obj.blob(blob_path)
        blob.download_to_filename(str(local_path))
        return pd.read_parquet(local_path)
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd /opt/quant && python3.12 -m pytest ml/tests/test_datasets.py::test_dataset_create_and_load -v
```

Expected: `PASSED` (dataset created in GCS, loaded back successfully)

- [ ] **Step 5: Run list_all test**

```bash
cd /opt/quant && python3.12 -m pytest ml/tests/test_datasets.py::test_dataset_list_all -v
```

Expected: `PASSED`

- [ ] **Step 6: Commit**

```bash
git add ml/datasets.py ml/tests/test_datasets.py
git commit -m "feat: add DatasetManager — versioned GCS Parquet datasets"
```

---

### Task 1.2: ModelRegistry — MLflow save/load/list/promote

**Files:**
- Create: `ml/registry.py`
- Create: `ml/tests/test_registry.py`

- [ ] **Step 1: Write test — save and load a model**

Create `ml/tests/test_registry.py`:

```python
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
    # Verify stage changed
    versions = ModelRegistry.list_versions("test_lgbm")
    staging_versions = [v for v in versions if v.get("stage") == "staging"]
    assert len(staging_versions) >= 1
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /opt/quant && python3.12 -m pytest ml/tests/test_registry.py -v
```

Expected: `FAILED - ModuleNotFoundError`

- [ ] **Step 3: Implement ModelRegistry**

Create `ml/registry.py`:

```python
"""ModelRegistry — MLflow Tracking + Model Registry wrapper.

Provides unified save/load/list/promote for all model consumers.
"""
from __future__ import annotations

import logging
import os
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Union

import mlflow
import mlflow.lightgbm
import yaml

logger = logging.getLogger(__name__)

MLFLOW_TRACKING_URI = os.environ.get("MLFLOW_TRACKING_URI", f"sqlite:///{Path.home()}/.mlflow/mlflow.db")
MLFLOW_ARTIFACT_ROOT = os.environ.get("MLFLOW_ARTIFACT_ROOT", str(Path("/opt/quant/models_artifacts")))


@dataclass
class ModelBundle:
    """Unified return type for all model consumers."""
    name: str
    version: int
    model: object               # lgb.Booster | sklearn Ridge | ...
    config: dict                # Full config.yaml content
    features: list[str]         # Feature column names
    metadata: dict              # metrics, dataset, git_commit, trained_at


class ModelRegistry:
    """MLflow Tracking + Model Registry wrapper."""

    _initialized = False

    @classmethod
    def _init(cls):
        if cls._initialized:
            return
        Path(MLFLOW_ARTIFACT_ROOT).mkdir(parents=True, exist_ok=True)
        mlflow.set_tracking_uri(MLFLOW_TRACKING_URI)
        mlflow.set_registry_uri(MLFLOW_TRACKING_URI)
        # Ensure artifact root is set for the experiment
        try:
            mlflow.set_experiment("quant")
        except Exception:
            pass
        cls._initialized = True

    @classmethod
    def save(cls, name: str, model, config: dict, metrics: dict,
             features: list[str], dataset_name: str, artifacts: dict = None) -> int:
        """Save model to MLflow. Returns version number."""
        cls._init()

        try:
            git_commit = subprocess.check_output(
                ["git", "rev-parse", "--short", "HEAD"], text=True, cwd="/opt/quant"
            ).strip()
        except Exception:
            git_commit = "unknown"

        with mlflow.start_run(run_name=f"{name}_{datetime.now(timezone.utc):%Y%m%d_%H%M%S}"):
            # Log params
            mlflow.log_param("model_name", name)
            mlflow.log_param("model_type", config.get("model", {}).get("type", "unknown"))
            mlflow.log_param("dataset", dataset_name)
            mlflow.log_param("n_features", len(features))
            mlflow.log_param("git_commit", git_commit)
            mlflow.log_param("label", config.get("data", {}).get("label", ""))

            # Log features as text
            mlflow.log_text("\n".join(features), "features.txt")

            # Log metrics
            for k, v in metrics.items():
                mlflow.log_metric(k, v)

            # Log config
            config_str = yaml.dump(config, default_flow_style=False)
            mlflow.log_text(config_str, "config.yaml")

            # Log model
            model_type = config.get("model", {}).get("type", "lightgbm")
            if model_type == "lightgbm":
                mlflow.lightgbm.log_model(model, "model")
            else:
                import joblib
                import tempfile
                with tempfile.NamedTemporaryFile(suffix=".pkl", delete=False) as f:
                    joblib.dump(model, f.name)
                    mlflow.log_artifact(f.name, "model")
                    os.unlink(f.name)

            # Log additional artifacts
            if artifacts:
                for art_name, art_path in artifacts.items():
                    mlflow.log_artifact(art_path, art_name)

            run_id = mlflow.active_run().info.run_id

        # Register model
        result = mlflow.register_model(
            f"runs:/{run_id}/model",
            name,
        )
        version = result.version

        # Set tags
        client = mlflow.tracking.MlflowClient()
        client.set_model_version_tag(name, version, "dataset", dataset_name)
        client.set_model_version_tag(name, version, "git_commit", git_commit)

        logger.info("Saved %s v%d (run=%s, IC=%.4f)", name, version, run_id,
                     metrics.get("val_ic", 0))
        return version

    @classmethod
    def load(cls, name: str, version: Union[int, str] = "latest") -> ModelBundle:
        """Load a registered model by name and version."""
        cls._init()

        if version == "latest":
            client = mlflow.tracking.MlflowClient()
            versions = client.get_latest_versions(name, stages=["production", "staging", "None"])
            if not versions:
                versions = client.get_latest_versions(name)
            if not versions:
                raise ValueError(f"No versions found for model '{name}'")
            version = versions[0].version

        version = int(version)

        # Load model URI
        model_uri = f"models:/{name}/{version}"
        try:
            model = mlflow.lightgbm.load_model(model_uri)
        except Exception:
            import joblib
            model = mlflow.sklearn.load_model(model_uri)

        # Load config from artifact
        client = mlflow.tracking.MlflowClient()
        mv = client.get_model_version(name, version)
        run_id = mv.run_id

        # Download artifacts
        artifact_dir = mlflow.artifacts.download_artifacts(run_id=run_id)
        config_path = Path(artifact_dir) / "config.yaml"
        features_path = Path(artifact_dir) / "features.txt"
        
        config = yaml.safe_load(config_path.read_text()) if config_path.exists() else {}
        features = features_path.read_text().strip().split("\n") if features_path.exists() else []

        # Build metadata
        run = client.get_run(run_id)
        metadata = {
            "run_id": run_id,
            "dataset": run.data.params.get("dataset", ""),
            "git_commit": run.data.params.get("git_commit", ""),
            "model_type": run.data.params.get("model_type", ""),
            **{k: v for k, v in run.data.metrics.items()},
        }

        logger.info("Loaded %s v%d (%d features)", name, version, len(features))
        return ModelBundle(
            name=name, version=version, model=model,
            config=config, features=features, metadata=metadata,
        )

    @classmethod
    def list_versions(cls, name: str) -> list[dict]:
        """List all versions for a model."""
        cls._init()
        client = mlflow.tracking.MlflowClient()
        results = []
        for mv in client.search_model_versions(f"name='{name}'"):
            run = client.get_run(mv.run_id) if mv.run_id else None
            results.append({
                "version": int(mv.version),
                "stage": mv.current_stage,
                "created_at": mv.creation_timestamp,
                "metrics": dict(run.data.metrics) if run else {},
                "dataset": mv.tags.get("dataset", ""),
            })
        return sorted(results, key=lambda x: x["version"], reverse=True)

    @classmethod
    def promote(cls, name: str, version: int, stage: str):
        """Transition model stage: staging → production → archived."""
        cls._init()
        client = mlflow.tracking.MlflowClient()
        client.transition_model_version_stage(name, version, stage)
        logger.info("Promoted %s v%d → %s", name, version, stage)

    @classmethod
    def get_latest_version(cls, name: str, stage: str = "production") -> int:
        """Get the latest version number in a given stage."""
        cls._init()
        client = mlflow.tracking.MlflowClient()
        versions = client.get_latest_versions(name, stages=[stage])
        if versions:
            return versions[0].version
        raise ValueError(f"No {stage} version of model '{name}'")
```

- [ ] **Step 4: Run tests**

```bash
cd /opt/quant && python3.12 -m pytest ml/tests/test_registry.py -v
```

Expected: All 3 tests `PASSED`

- [ ] **Step 5: Commit**

```bash
git add ml/registry.py ml/tests/test_registry.py
git commit -m "feat: add ModelRegistry — MLflow save/load/list/promote"
```

---

### Task 1.3: OptunaTuner — config-driven hyperparameter tuning

**Files:**
- Create: `ml/tuner.py`
- Create: `ml/tests/test_tuner.py`

- [ ] **Step 1: Write test**

Create `ml/tests/test_tuner.py`:

```python
"""Tests for OptunaTuner."""
import numpy as np
import pytest
import tempfile
from pathlib import Path
import yaml

def test_tuner_runs_and_returns_bundle():
    """Tuner should complete trials and return ModelBundle."""
    # Create a minimal config
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
            "cv_folds": 2,
        },
    }

    # Write config to temp file
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        yaml.dump(config, f)
        config_path = f.name

    # Create dummy data
    np.random.seed(42)
    n = 200
    features = np.random.randn(n, 5)
    feature_cols = [f"feat_{i}" for i in range(5)]
    true_coef = np.array([0.3, -0.1, 0.05, 0.0, 0.0])
    y = features @ true_coef + np.random.randn(n) * 0.05

    import pandas as pd
    df = pd.DataFrame(features, columns=feature_cols)
    df["fwd_ret_5d"] = y
    df["date"] = pd.date_range("2025-01-01", periods=n)

    from ml.tuner import OptunaTuner
    from ml.datasets import DatasetBundle

    train = df.iloc[:100]
    val = df.iloc[100:160]
    test = df.iloc[160:]

    dummy_bundle = DatasetBundle(
        name="test_dummy_data",
        meta={"features": feature_cols},
        train=train, val=val, test=test,
    )

    # Run tuner with the dummy data directly
    tuner = OptunaTuner(config_path)
    bundle = tuner._tune_with_data(dummy_bundle, n_trials=3)

    assert bundle.name == "test_optuna"
    assert bundle.version > 0
    assert len(bundle.features) > 0
    assert bundle.metadata.get("val_rmse", 0) > 0

    Path(config_path).unlink()
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /opt/quant && python3.12 -m pytest ml/tests/test_tuner.py -v
```

Expected: `FAILED - ModuleNotFoundError`

- [ ] **Step 3: Implement OptunaTuner**

Create `ml/tuner.py`:

```python
"""OptunaTuner — config-driven hyperparameter tuning integrated with ModelRegistry."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import numpy as np
import optuna
import yaml
from optuna.samplers import TPESampler

from ml.registry import ModelRegistry, ModelBundle
from ml.datasets import DatasetManager, DatasetBundle

logger = logging.getLogger(__name__)


class OptunaTuner:
    """Hyperparameter tuning with Optuna.

    Reads model config.yaml, runs trials, saves best model to ModelRegistry.
    """

    def __init__(self, config_path: str):
        self.config_path = Path(config_path)
        if not self.config_path.exists():
            raise FileNotFoundError(f"Config not found: {config_path}")
        with open(self.config_path) as f:
            self.config = yaml.safe_load(f)

    def tune(self, n_trials: int = None) -> ModelBundle:
        """Run Optuna study, save best model to ModelRegistry."""
        n = n_trials or self.config.get("training", {}).get("n_trials", 50)
        dataset_name = self.config.get("data", {}).get("dataset")
        
        if dataset_name:
            dataset = DatasetManager.load(dataset_name)
        else:
            raise ValueError("config.data.dataset is required for tuning")

        return self._tune_with_data(dataset, n_trials=n)

    def _tune_with_data(self, dataset: DatasetBundle, n_trials: int = 50) -> ModelBundle:
        """Internal method: tune using already-loaded dataset (for testing)."""
        model_cfg = self.config.get("model", {})
        hp_cfg = self.config.get("hyperparams", {})
        training_cfg = self.config.get("training", {})
        data_cfg = self.config.get("data", {})

        model_name = model_cfg.get("name", "unknown")
        model_type = model_cfg.get("type", "lightgbm")
        label = data_cfg.get("label", "fwd_ret_5d")
        direction = training_cfg.get("optuna_direction", "maximize")
        metric_name = training_cfg.get("optuna_metric", "val_ic")

        # Feature columns (exclude symbol, date, label)
        exclude = {"symbol", "date", label}
        feature_cols = [c for c in dataset.train.columns if c not in exclude]
        
        search_space = hp_cfg.get("search_space", {})
        fixed_params = hp_cfg.get("fixed", {})

        def objective(trial: optuna.Trial) -> float:
            params = {**fixed_params}
            for name, spec in search_space.items():
                ptype = spec["type"]
                low = spec["low"]
                high = spec["high"]
                if ptype == "int":
                    params[name] = trial.suggest_int(name, low, high)
                elif ptype == "loguniform":
                    params[name] = trial.suggest_float(name, low, high, log=True)
                elif ptype == "uniform":
                    params[name] = trial.suggest_float(name, low, high)

            # Train LightGBM
            import lightgbm as lgb

            train_X = dataset.train[feature_cols].values
            train_y = dataset.train[label].values
            val_X = dataset.val[feature_cols].values
            val_y = dataset.val[label].values

            train_data = lgb.Dataset(train_X, label=train_y)
            val_data = lgb.Dataset(val_X, label=val_y, reference=train_data)

            model = lgb.train(
                params,
                train_data,
                valid_sets=[val_data],
                callbacks=[lgb.early_stopping(10), lgb.log_evaluation(0)],
            )

            preds = model.predict(val_X)
            if metric_name == "val_ic":
                from scipy.stats import spearmanr
                ic, _ = spearmanr(preds, val_y)
                # Optuna minimizes by default; negative IC means higher IC is better
                if direction == "maximize":
                    return float(ic)  # positive IC → good (minimize → maximize IC)
                return -float(abs(ic))  # minimize -|IC|
            else:
                from sklearn.metrics import mean_squared_error
                rmse = np.sqrt(mean_squared_error(val_y, preds))
                return float(rmse)

        # Run study
        study = optuna.create_study(
            direction="maximize" if direction == "maximize" else "minimize",
            sampler=TPESampler(seed=42),
        )
        
        logger.info("Starting Optuna study: %s, %d trials", model_name, n_trials)
        study.optimize(objective, n_trials=n_trials, show_progress_bar=True)

        # Train best model on full train set
        best_params = {**fixed_params, **study.best_params}
        logger.info("Best params: %s", study.best_params)
        logger.info("Best %s: %.4f", metric_name, study.best_value)

        import lightgbm as lgb
        train_X = dataset.train[feature_cols].values
        train_y = dataset.train[label].values
        val_X = dataset.val[feature_cols].values
        val_y = dataset.val[label].values

        train_data = lgb.Dataset(train_X, label=train_y)
        val_data = lgb.Dataset(val_X, label=val_y, reference=train_data)
        best_model = lgb.train(best_params, train_data, valid_sets=[val_data])

        # Evaluate on test set
        test_X = dataset.test[feature_cols].values
        test_y = dataset.test[label].values
        test_preds = best_model.predict(test_X)
        from scipy.stats import spearmanr
        test_ic, _ = spearmanr(test_preds, test_y)
        from sklearn.metrics import mean_squared_error
        test_rmse = np.sqrt(mean_squared_error(test_y, test_preds))
        val_preds = best_model.predict(val_X)
        val_ic, _ = spearmanr(val_preds, val_y)
        val_rmse = np.sqrt(mean_squared_error(val_y, val_preds))
        train_preds = best_model.predict(train_X)
        train_rmse = np.sqrt(mean_squared_error(train_y, train_preds))

        metrics = {
            "val_ic": float(val_ic),
            "val_rmse": float(val_rmse),
            "test_ic": float(test_ic),
            "test_rmse": float(test_rmse),
            "train_rmse": float(train_rmse),
            "n_features": len(feature_cols),
            "optuna_best_value": float(study.best_value),
            "n_trials": n_trials,
        }

        dataset_name = self.config.get("data", {}).get("dataset", "")

        version = ModelRegistry.save(
            name=model_name,
            model=best_model,
            config=self.config,
            metrics=metrics,
            features=feature_cols,
            dataset_name=dataset_name,
        )

        # Promote to production
        ModelRegistry.promote(model_name, version, "production")

        return ModelRegistry.load(model_name, version)
```

- [ ] **Step 4: Run tests**

```bash
cd /opt/quant && python3.12 -m pytest ml/tests/test_tuner.py -v
```

Expected: `PASSED`

- [ ] **Step 5: Commit**

```bash
git add ml/tuner.py ml/tests/test_tuner.py
git commit -m "feat: add OptunaTuner — config-driven hyperparameter tuning"
```

---

### Task 1.4: Initial Model Configuration

**Files:**
- Create: `models/momentum_lgbm/v1/config.yaml`

- [ ] **Step 1: Create model config**

```bash
mkdir -p /opt/quant/models/momentum_lgbm/v1
```

Create `models/momentum_lgbm/v1/config.yaml`:

```yaml
model:
  name: "momentum_lgbm"
  type: "lightgbm"
  description: "US top 20 momentum + F10 factors, LightGBM regression"

factors:
  source: "registry"
  top_n: 20
  min_ic: 0.02
  exclude: ["fwd_ret_5d", "fwd_ret_20d"]

hyperparams:
  search_space:
    num_leaves: {type: "int", low: 15, high: 127}
    learning_rate: {type: "loguniform", low: 0.005, high: 0.1}
    feature_fraction: {type: "uniform", low: 0.5, high: 1.0}
    bagging_fraction: {type: "uniform", low: 0.5, high: 1.0}
    min_child_samples: {type: "int", low: 10, high: 200}
    lambda_l2: {type: "loguniform", low: 0.00000001, high: 10.0}
  fixed:
    objective: "regression"
    metric: "rmse"
    boosting: "gbdt"
    early_stopping_rounds: 100
    random_state: 42
    verbose: -1

data:
  dataset: "us_top20_v1"
  label: "fwd_ret_5d"

training:
  optuna_direction: "maximize"
  optuna_metric: "val_ic"
  n_trials: 50
  cv_folds: 5
```

- [ ] **Step 2: Commit**

```bash
git add models/
git commit -m "feat: add initial model config — momentum_lgbm v1"
```

---

### Task 1.5: Create Initial Datasets + First Model Training

**Files:**
- Modify: `ml/__init__.py`

- [ ] **Step 1: Update `ml/__init__.py` to export new classes**

Edit `ml/__init__.py`:

```python
"""ML package — model training, registry, tuning, and dataset management."""

from .trainer import ModelTrainer
from .registry import ModelRegistry, ModelBundle
from .tuner import OptunaTuner
from .datasets import DatasetManager, DatasetConfig, DatasetBundle

__all__ = [
    "ModelTrainer",
    "ModelRegistry",
    "ModelBundle",
    "OptunaTuner",
    "DatasetManager",
    "DatasetConfig",
    "DatasetBundle",
]
```

- [ ] **Step 2: Verify imports**

```bash
cd /opt/quant && python3.12 -c "from ml import ModelTrainer, ModelRegistry, ModelBundle, OptunaTuner, DatasetManager, DatasetConfig, DatasetBundle; print('All imports OK')"
```

Expected: `All imports OK`

- [ ] **Step 3: Create us_top20_v1 dataset**

```bash
cd /opt/quant && python3.12 -c "
from ml.datasets import DatasetManager, DatasetConfig

config = DatasetConfig(
    market='us',
    symbols=['AAPL','MSFT','NVDA','AMZN','GOOGL','META','TSLA','AMD','NFLX','INTC',
             'JPM','V','JNJ','WMT','PG','MA','HD','BAC','DIS','CRM'],
    features='from_registry_top_20',
    label='fwd_ret_5d',
    train_range=('2020-01-01','2023-12-31'),
    val_range=('2024-01-01','2024-12-31'),
    test_range=('2025-01-01','2025-12-31'),
)
name = DatasetManager.create('us_top20_v1', config)
print(f'Created: {name}')
# Verify
bundle = DatasetManager.load(name)
print(f'  train={len(bundle.train):,} val={len(bundle.val):,} test={len(bundle.test):,}')
print(f'  features={bundle.meta[\"n_features\"]}')
"
```

Expected: Dataset created and verified with row counts

- [ ] **Step 4: Run first model tuning**

```bash
cd /opt/quant && python3.12 -m ml.tuner models/momentum_lgbm/v1/config.yaml --trials 50
```

Expected: Optuna completes 50 trials, saves best model to MLflow, reports metrics

- [ ] **Step 5: Verify model is loadable**

```bash
cd /opt/quant && python3.12 -c "
from ml.registry import ModelRegistry
bundle = ModelRegistry.load('momentum_lgbm', version='latest')
print(f'Loaded: {bundle.name} v{bundle.version}')
print(f'  features: {len(bundle.features)}')
print(f'  val_ic: {bundle.metadata.get(\"val_ic\", \"N/A\")}')
print(f'  test_ic: {bundle.metadata.get(\"test_ic\", \"N/A\")}')
"
```

Expected: Model loaded with version, feature count, and IC metrics

- [ ] **Step 6: Commit**

```bash
git add ml/__init__.py
git commit -m "feat: update ml/__init__ exports, create initial dataset + model v1"
```

---

## Phase 2: Live Loop

### Task 2.1: Observer — logs, snapshots, trade records

**Files:**
- Create: `live/__init__.py`
- Create: `live/observer.py`

- [ ] **Step 1: Create package init**

```bash
mkdir -p /opt/quant/live
```

Create `live/__init__.py`:

```python
"""Live trading loop package."""
```

- [ ] **Step 2: Implement Observer**

Create `live/observer.py`:

```python
"""Observer — passive recording of trading activity.

Writes trades, signals, position snapshots, equity curve to CSV/JSON/log.
Never throws — failures are logged but never propagate to the main loop.
"""
from __future__ import annotations

import csv
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)


class Observer:
    """Passive observer that records trading state to disk."""

    def __init__(self, output_dir: str, snapshot_interval: int = 60):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.snapshot_interval = snapshot_interval
        self._last_snapshot = None
        self._bar_count = 0

        # Open file handles
        self._trades_file = open(self.output_dir / "trades.csv", "w", newline="")
        self._trades_writer = csv.writer(self._trades_file)
        self._trades_writer.writerow(["time", "symbol", "side", "qty", "price", "commission"])

        self._signals_file = open(self.output_dir / "signals.csv", "w", newline="")
        self._signals_writer = csv.writer(self._signals_file)
        self._signals_writer.writerow(["time", "symbol", "side", "score", "rank"])

        self._snapshots_file = open(self.output_dir / "positions_snapshot.csv", "w", newline="")
        self._snapshots_writer = csv.writer(self._snapshots_file)
        self._snapshots_writer.writerow(["timestamp", "symbol", "qty", "price", "cost_basis", "mkt_value", "pnl_pct"])

        self._equity_file = open(self.output_dir / "equity_curve.csv", "w", newline="")
        self._equity_writer = csv.writer(self._equity_file)
        self._equity_writer.writerow(["timestamp", "equity", "cash", "portfolio_value", "return_pct"])

        self._alert_file = open(self.output_dir / "alerts.log", "a")
        self._initial_equity = None

    def snapshot_due(self, now: datetime) -> bool:
        """Check if enough time has passed since last snapshot."""
        if self._last_snapshot is None:
            return True
        return (now - self._last_snapshot).total_seconds() >= self.snapshot_interval

    def record_signal(self, timestamp, symbol: str, side: str, score: float = 0.0, rank: int = 0):
        """Record a strategy signal."""
        try:
            self._signals_writer.writerow([timestamp, symbol, side, score, rank])
        except Exception:
            logger.exception("Observer: failed to record signal")

    def record_trade(self, timestamp, symbol: str, side: str, qty: int, price: float, commission: float = 0.0):
        """Record a filled trade."""
        try:
            self._trades_writer.writerow([timestamp, symbol, side, qty, price, commission])
        except Exception:
            logger.exception("Observer: failed to record trade")

    def record_bar(self, timestamp, equity: float, cash: float, portfolio_value: float, return_pct: float = 0.0):
        """Record equity curve data point."""
        try:
            if self._initial_equity is None:
                self._initial_equity = equity
            ret = (equity / self._initial_equity - 1.0) * 100 if self._initial_equity else 0.0
            self._equity_writer.writerow([timestamp, equity, cash, portfolio_value, ret])
            self._bar_count += 1
        except Exception:
            logger.exception("Observer: failed to record bar")

    def snapshot_portfolio(self, timestamp, positions: list[dict]):
        """Write current position snapshot.
        
        positions: list of dicts with keys {symbol, qty, price, cost_basis, mkt_value, pnl_pct}
        """
        try:
            self._last_snapshot = timestamp
            for pos in positions:
                self._snapshots_writer.writerow([
                    timestamp,
                    pos.get("symbol", ""),
                    pos.get("qty", 0),
                    pos.get("price", 0.0),
                    pos.get("cost_basis", 0.0),
                    pos.get("mkt_value", 0.0),
                    pos.get("pnl_pct", 0.0),
                ])
        except Exception:
            logger.exception("Observer: failed to snapshot portfolio")

    def record_alert(self, timestamp, level: str, message: str):
        """Write an alert."""
        try:
            self._alert_file.write(f"{timestamp} [{level}] {message}\n")
        except Exception:
            logger.exception("Observer: failed to record alert")

    def close(self):
        """Close all file handles."""
        for f in [self._trades_file, self._signals_file, self._snapshots_file, self._equity_file, self._alert_file]:
            try:
                f.close()
            except Exception:
                pass
```

- [ ] **Step 3: Verify imports**

```bash
cd /opt/quant && python3.12 -c "from live.observer import Observer; print('OK')"
```

Expected: `OK`

- [ ] **Step 4: Commit**

```bash
git add live/__init__.py live/observer.py
git commit -m "feat: add Observer — passive trade/signal/equity recording"
```

---

### Task 2.2: Reporter — HTML report + charts

**Files:**
- Create: `live/reporter.py`

- [ ] **Step 1: Implement Reporter**

Create `live/reporter.py`:

```python
"""Reporter — generate post-run HTML report with matplotlib charts."""
from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

logger = logging.getLogger(__name__)


class Reporter:
    """Generate HTML summary report with embedded charts at end of run."""

    def __init__(self, output_dir: str):
        self.output_dir = Path(output_dir)

    def generate(self):
        """Generate report.html with equity curve, drawdown, pie chart, summary."""
        logger.info("Generating report...")

        # Load data
        equity_df = pd.read_csv(self.output_dir / "equity_curve.csv", parse_dates=["timestamp"])
        trades_df = pd.read_csv(self.output_dir / "trades.csv", parse_dates=["time"])

        if equity_df.empty:
            logger.warning("No equity data for report")
            return

        charts = []

        # 1. Equity curve
        fig, ax = plt.subplots(figsize=(10, 4))
        ax.plot(equity_df["timestamp"], equity_df["equity"], color="#2E86AB", linewidth=1.5)
        ax.set_title("Equity Curve")
        ax.set_ylabel("Equity ($)")
        ax.grid(alpha=0.3)
        charts.append(self._save_chart(fig, "equity_curve.png"))

        # 2. Drawdown
        equity = equity_df["equity"].values
        peak = pd.Series(equity).cummax()
        dd = (equity - peak) / peak * 100
        fig, ax = plt.subplots(figsize=(10, 3))
        ax.fill_between(equity_df["timestamp"], 0, dd, color="#D81159", alpha=0.5)
        ax.set_title("Drawdown (%)")
        ax.set_ylabel("Drawdown %")
        ax.grid(alpha=0.3)
        charts.append(self._save_chart(fig, "drawdown.png"))

        # 3. Position concentration pie chart (last snapshot)
        snap_path = self.output_dir / "positions_snapshot.csv"
        if snap_path.exists():
            snap_df = pd.read_csv(snap_path, parse_dates=["timestamp"])
            if not snap_df.empty:
                last_ts = snap_df["timestamp"].max()
                current = snap_df[snap_df["timestamp"] == last_ts]
                if not current.empty:
                    fig, ax = plt.subplots(figsize=(6, 6))
                    labels = current["symbol"].tolist()
                    values = current["mkt_value"].tolist()
                    ax.pie(values, labels=labels, autopct="%1.1f%%", startangle=90)
                    ax.set_title("Portfolio Allocation")
                    charts.append(self._save_chart(fig, "allocation.png"))

        # 4. Signal frequency
        sig_path = self.output_dir / "signals.csv"
        if sig_path.exists():
            sig_df = pd.read_csv(sig_path)
            if not sig_df.empty:
                buy_counts = sig_df[sig_df["side"] == "BUY"]["symbol"].value_counts()
                if not buy_counts.empty:
                    fig, ax = plt.subplots(figsize=(10, 4))
                    buy_counts.plot(kind="bar", ax=ax, color="#A1C181")
                    ax.set_title("Buy Signal Frequency by Symbol")
                    ax.set_xlabel("Symbol")
                    ax.set_ylabel("Count")
                    charts.append(self._save_chart(fig, "signals.png"))

        # 5. Summary metrics
        total_return = (equity_df["equity"].iloc[-1] / equity_df["equity"].iloc[0] - 1) * 100
        n_days = len(equity_df)
        if len(trades_df) > 0:
            win_trades = trades_df[trades_df["side"] == "SELL"]
            total_trades = len(win_trades)
        else:
            total_trades = 0

        max_dd = dd.min()
        max_equity = equity.max()
        min_equity = equity.min()

        # Render HTML
        chart_html = "\n".join(
            f'<h3>{name}</h3><img src="{path}" style="max-width:800px;">'
            for name, path in charts
        )

        html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Live Run Report</title>
<style>
body {{ font-family: -apple-system, sans-serif; margin: 40px; color: #333; }}
h1 {{ color: #2E86AB; }}
table {{ border-collapse: collapse; width: 100%; max-width: 600px; }}
td, th {{ padding: 8px 12px; text-align: left; border-bottom: 1px solid #ddd; }}
th {{ background: #f5f5f5; }}
</style></head><body>
<h1>📊 Live Run Report</h1>
<h2>Summary</h2>
<table>
<tr><th>Metric</th><th>Value</th></tr>
<tr><td>Total Return</td><td>{total_return:+.2f}%</td></tr>
<tr><td>Trading Days</td><td>{n_days}</td></tr>
<tr><td>Total Trades</td><td>{total_trades}</td></tr>
<tr><td>Max Drawdown</td><td>{max_dd:.2f}%</td></tr>
<tr><td>Max Equity</td><td>${max_equity:,.0f}</td></tr>
<tr><td>Min Equity</td><td>${min_equity:,.0f}</td></tr>
</table>
<h2>Charts</h2>
{chart_html}
</body></html>"""

        report_path = self.output_dir / "report.html"
        report_path.write_text(html)
        logger.info("Report written to %s", report_path)

    def _save_chart(self, fig, name: str):
        chart_path = self.output_dir / name
        fig.savefig(chart_path, dpi=100, bbox_inches="tight")
        plt.close(fig)
        return (name.replace(".png", "").replace("_", " ").title(), name)
```

- [ ] **Step 2: Verify imports**

```bash
cd /opt/quant && python3.12 -c "from live.reporter import Reporter; print('OK')"
```

Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add live/reporter.py
git commit -m "feat: add Reporter — HTML report with matplotlib charts"
```

---

### Task 2.3: Config Loader

**Files:**
- Create: `live/config.py`
- Create: `live/configs/paper_us.yaml`

- [ ] **Step 1: Implement config loader**

Create `live/config.py`:

```python
"""Live Loop configuration loader and validation."""
from __future__ import annotations

import yaml
from pathlib import Path
from typing import Any


REQUIRED_KEYS = {
    "live": ["mode", "market"],
    "broker": [],
    "strategy": ["name"],
    "schedule": ["bar_interval"],
    "risk": [],
    "observer": [],
}

DEFAULT_VALUES = {
    "broker": {
        "paper": {
            "initial_capital": 100000,
            "slippage_bps": 5,
            "commission_bps": 1,
            "min_commission": 1.0,
        },
        "live": {
            "type": "futu_stock",
            "host": "127.0.0.1",
            "port": 11111,
            "max_position_pct": 0.2,
        },
    },
    "schedule": {
        "pre_market_warmup": 300,
        "bar_interval": 300,
        "market_close_offset": 600,
    },
    "risk": {
        "max_drawdown": 0.15,
        "max_daily_loss": 0.05,
        "position_size_pct": 0.2,
    },
    "observer": {
        "log_dir": "output/live/",
        "snapshot_interval": 60,
        "trade_log": True,
        "equity_curve": True,
    },
    "dashboard": {
        "port": 8090,
        "websocket": True,
    },
}


def load_config(path: str) -> dict:
    """Load and validate a YAML config file, applying defaults for missing keys."""
    with open(path) as f:
        config = yaml.safe_load(f)

    # Apply defaults recursively
    _apply_defaults(config, DEFAULT_VALUES)

    # Validate required
    mode = config.get("live", {}).get("mode", "paper")
    if mode not in ("paper", "live"):
        raise ValueError(f"live.mode must be 'paper' or 'live', got '{mode}'")

    market = config.get("live", {}).get("market", "us")
    if market not in ("us", "hk", "crypto"):
        raise ValueError(f"live.market must be us/hk/crypto, got '{market}'")

    # Generate output subdir
    from datetime import datetime, timezone
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    subdir = f"{market}_{mode}_{ts}"
    output_root = config.get("live", {}).get("output_dir", "output/live/")
    config["_output_dir"] = str(Path(output_root) / subdir)

    return config


def _apply_defaults(config: dict, defaults: dict):
    """Apply default values for missing keys, recursively."""
    for key, value in defaults.items():
        if key not in config:
            config[key] = value
        elif isinstance(value, dict) and isinstance(config.get(key), dict):
            _apply_defaults(config[key], value)
```

- [ ] **Step 2: Create paper trading config**

```bash
mkdir -p /opt/quant/live/configs
```

Create `live/configs/paper_us.yaml`:

```yaml
live:
  mode: paper
  market: us
  output_dir: output/live/

broker:
  paper:
    initial_capital: 100000
    slippage_bps: 5
    commission_bps: 1
    min_commission: 1.0

strategy:
  name: MLPredStrategy
  model_name: momentum_lgbm
  model_version: latest
  top_k: 5
  rebalance_every: 5
  fallback_strategy: SimpleMomentum

schedule:
  pre_market_warmup: 300
  bar_interval: 300
  market_close_offset: 600

risk:
  max_drawdown: 0.15
  max_daily_loss: 0.05
  position_size_pct: 0.2

observer:
  log_dir: output/live/
  snapshot_interval: 60
  trade_log: true
  equity_curve: true

dashboard:
  port: 8090
  websocket: true
```

- [ ] **Step 3: Verify loading**

```bash
cd /opt/quant && python3.12 -c "
from live.config import load_config
c = load_config('live/configs/paper_us.yaml')
print('mode:', c['live']['mode'])
print('market:', c['live']['market'])
print('output:', c['_output_dir'])
print('broker capital:', c['broker']['paper']['initial_capital'])
"
```

Expected: `mode: paper`, `market: us`, valid output path

- [ ] **Step 4: Commit**

```bash
git add live/config.py live/configs/
git commit -m "feat: add LiveLoop config loader + paper_us config"
```

---

### Task 2.4: LiveRunner main loop

**Files:**
- Create: `live/runner.py`

- [ ] **Step 1: Implement LiveRunner**

Create `live/runner.py`:

```python
"""LiveRunner — unified paper/live trading loop."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from live.config import load_config
from live.observer import Observer
from live.reporter import Reporter

logger = logging.getLogger(__name__)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)


class LiveRunner:
    """Unified runner for paper and live trading.

    Usage:
        runner = LiveRunner("live/configs/paper_us.yaml")
        runner.run()
    """

    def __init__(self, config_path: str):
        self.config = load_config(config_path)
        self.mode = self.config["live"]["mode"]
        self.market = self.config["live"]["market"]
        self.output_dir = self.config["_output_dir"]
        self.broker = None
        self.strategy = None
        self.observer = None
        self.reporter = None

    def run(self):
        """Execute the full trading lifecycle."""
        try:
            self._init_components()
            # For paper mode: backfill historical run (no waiting for market open)
            if self.mode == "paper":
                self._run_paper_loop()
            else:
                self._wait_for_market_open()
                self._run_live_loop()
        except KeyboardInterrupt:
            logger.info("Interrupted — shutting down")
        except Exception:
            logger.exception("LiveRunner failed")
            raise
        finally:
            self._shutdown()

    def _init_components(self):
        """Initialize all components."""
        logger.info("Initializing LiveRunner: %s %s", self.market.upper(), self.mode.upper())

        # 1. Broker
        if self.mode == "paper":
            from oms.broker import PaperBroker
            paper_cfg = self.config["broker"]["paper"]
            self.broker = PaperBroker(initial_capital=paper_cfg["initial_capital"])
            self.slippage_bps = paper_cfg.get("slippage_bps", 5)
            self.commission_bps = paper_cfg.get("commission_bps", 1)
            self.min_commission = paper_cfg.get("min_commission", 1.0)
        else:
            live_cfg = self.config["broker"]["live"]
            broker_type = live_cfg["type"]
            if broker_type == "futu_stock":
                from oms.broker.futu_stock_broker import FutuStockBroker
                self.broker = FutuStockBroker(
                    host=live_cfg["host"],
                    port=live_cfg["port"],
                )
            elif broker_type == "alpaca":
                from oms.broker.alpaca_broker import AlpacaBroker
                self.broker = AlpacaBroker()
            else:
                raise ValueError(f"Unknown broker type: {broker_type}")
            self.slippage_bps = 0  # Live: exchange slippage
            self.commission_bps = live_cfg.get("commission_bps", 1)

        # 2. Risk Gateway
        from oms.risk_gateway import RiskGateway
        from engine.risk import RiskEngine
        risk_cfg = self.config["risk"]
        rules = self._build_risk_rules(risk_cfg)
        self.risk_gateway = RiskGateway(
            rules=rules, broker=self.broker, alert_manager=None,
        )

        # 3. Strategy
        strategy_name = self.config["strategy"]["name"]
        strategy_cfg = self.config["strategy"]
        if strategy_name == "MLPredStrategy":
            from strategies.ml_pred import MLPredStrategy
            self.strategy = MLPredStrategy(
                market=self.market,
                top_k=strategy_cfg.get("top_k", 5),
                rebalance_every=strategy_cfg.get("rebalance_every", 5),
                model_name=strategy_cfg.get("model_name", "momentum_lgbm"),
                model_version=strategy_cfg.get("model_version", "latest"),
            )
        elif strategy_name == "SimpleMomentum":
            from paper.strategies import SimpleMomentum
            self.strategy = SimpleMomentum(
                top_k=strategy_cfg.get("top_k", 5),
                rebalance_every=strategy_cfg.get("rebalance_every", 5),
            )
        else:
            raise ValueError(f"Unknown strategy: {strategy_name}")

        # 4. Observer
        obs_cfg = self.config["observer"]
        self.observer = Observer(
            output_dir=self.output_dir,
            snapshot_interval=obs_cfg.get("snapshot_interval", 60),
        )
        # Save config copy
        import yaml
        (self.output_dir / "config.yaml").write_text(
            yaml.dump(self.config, default_flow_style=False)
        )

        # 5. Reporter
        self.reporter = Reporter(output_dir=self.output_dir)

        # 6. Dashboard (optional)
        dash_cfg = self.config.get("dashboard", {})
        if dash_cfg.get("websocket"):
            self._start_dashboard(dash_cfg.get("port", 8090))

        # 7. Order Manager
        from oms.manager import OrderManager
        from oms.position import PositionTracker
        self.order_manager = OrderManager(self.broker)
        self.position_tracker = PositionTracker(self.broker)

        logger.info("All components initialized")

    def _run_paper_loop(self):
        """Run paper trading over historical data."""
        from datetime import date
        strategy_cfg = self.config["strategy"]

        # Determine date range
        today = date.today()
        start = date(today.year, 1, 1)
        end = today

        logger.info("Paper run: %s → %s", start, end)

        from engine.strategy import StrategyContext
        from engine.data import DataFrameSource
        from google.cloud import bigquery
        import pandas as pd

        # Load data from BQ
        bq = bigquery.Client(project="deductive-notch-495015-c2")
        table = "us_bars_1d" if self.market == "us" else (
            "hk_bars_1d" if self.market == "hk" else "crypto_bars_1d"
        )
        symbols = strategy_cfg.get("symbols", None)
        sym_filter = ""
        if symbols:
            sym_list = ", ".join(f"'{s}'" for s in symbols)
            sym_filter = f" AND symbol IN ({sym_list})"

        query = f"""
            SELECT symbol, timestamp, open, high, low, close, volume
            FROM quant.{table}
            WHERE timestamp BETWEEN '{start}' AND '{end}'
            {sym_filter}
            ORDER BY symbol, timestamp
        """
        df = bq.query(query).result().to_dataframe()
        df["timestamp"] = pd.to_datetime(df["timestamp"])

        src = DataFrameSource(df)
        symbols_list = sorted(df["symbol"].unique().tolist())

        # Init strategy
        from engine.portfolio import Portfolio
        portfolio = Portfolio(initial_capital=self.config["broker"]["paper"]["initial_capital"])
        ctx = StrategyContext(data=src, portfolio=portfolio, config={
            "symbols": symbols_list,
            **strategy_cfg,
        })
        self.strategy.on_init(ctx)

        # Bar loop
        self._initial_equity = portfolio.total_equity

        for bar_idx in range(len(src)):
            bar_data = src.iloc(bar_idx)
            ts = src.timestamp[bar_idx]
            portfolio.mark_and_record(ts, bar_data)

            # Snapshot
            if self.observer.snapshot_due(ts):
                pos_list = []
                for sym, pos in portfolio.positions.items():
                    if hasattr(pos, "size") and pos.size > 0:
                        price = bar_data["close"].get(sym, 0)
                        cb = getattr(pos, "cost_basis", 0) or getattr(pos, "avg_price", 0)
                        pos_list.append({
                            "symbol": sym,
                            "qty": pos.size,
                            "price": price,
                            "cost_basis": cb,
                            "mkt_value": pos.size * price,
                            "pnl_pct": (price / cb - 1) * 100 if cb > 0 else 0,
                        })
                self.observer.snapshot_portfolio(ts, pos_list)

            signals = self.strategy.on_bar(ctx, bar_idx)
            if not signals:
                continue

            from oms.bridge import convert_signal

            # Normalize weights
            n_buy = sum(1 for s in signals if s.side in ("buy", "target"))
            if n_buy > 0:
                for s in signals:
                    if s.side in ("buy", "target") and s.weight is None:
                        s.weight = 1.0 / n_buy

            for sig in signals:
                price = bar_data["close"].get(sig.symbol, 100.0)
                sd = convert_signal(sig, portfolio, price_est=price)

                slippage = price * self.slippage_bps / 10000
                exec_price = price + slippage if sd["side"] == "buy" else price - slippage

                # Cash constraint
                if sd["side"] == "buy":
                    max_qty = max(0, int(portfolio.cash / (exec_price * 1.0001)))
                    old_qty = sd["qty"]
                    sd["qty"] = min(sd["qty"], max_qty)
                    if sd["qty"] <= 0:
                        continue

                commission = max(
                    self.min_commission,
                    self.commission_bps / 10000 * sd["qty"] * exec_price,
                )

                tracked = asyncio.run(
                    self.order_manager.submit(
                        sd["symbol"], sd["side"], sd["qty"],
                        strategy_name=strategy_name,
                        signal_id=sd.get("signal_id"),
                    )
                )

                self.observer.record_signal(ts, sd["symbol"], sd["side"], 0, 1)

                if tracked and tracked.filled_qty > 0:
                    from engine.portfolio import Position
                    pos = portfolio.positions.get(tracked.symbol)
                    if pos is None:
                        pos = Position(symbol=tracked.symbol)
                        portfolio.positions[tracked.symbol] = pos
                    delta = tracked.filled_qty if tracked.side == "buy" else -tracked.filled_qty
                    pos.add(delta, exec_price)
                    if tracked.side == "buy":
                        portfolio.cash -= (exec_price * tracked.filled_qty + commission)
                    else:
                        portfolio.cash += (exec_price * tracked.filled_qty - commission)

                    self.observer.record_trade(
                        ts, tracked.symbol, tracked.side,
                        int(tracked.filled_qty), exec_price, commission,
                    )

            eq = portfolio._mark_to_market(bar_data)
            self.observer.record_bar(ts, eq, portfolio.cash, eq - portfolio.cash)

        # End of run
        final_eq = portfolio._mark_to_market(src.iloc(len(src) - 1))
        logger.info("Paper run complete — final equity: $%.0f", final_eq)

    def _run_live_loop(self):
        """Run live trading loop with real-time data."""
        raise NotImplementedError("Live loop (real-time) — pending WebSocket data feed integration")

    def _wait_for_market_open(self):
        """Wait until market opens."""
        # Placeholder — implement with market schedule
        logger.info("Waiting for market open...")

    def _build_risk_rules(self, risk_cfg: dict) -> list:
        """Build risk rule objects from config."""
        # Placeholder — implement when risk rules are wired
        return []

    def _start_dashboard(self, port: int):
        """Start dashboard in background thread."""
        try:
            import threading
            from dashboard.api import app, configure
            import uvicorn
            
            configure(
                broker=self.broker,
                order_manager=self.order_manager,
                position_tracker=self.position_tracker,
            )

            def _serve():
                uvicorn.run(app, host="0.0.0.0", port=port, log_level="warning")

            t = threading.Thread(target=_serve, daemon=True)
            t.start()
            logger.info("Dashboard started on port %d", port)
        except Exception:
            logger.exception("Dashboard start failed (non-fatal)")

    def _shutdown(self):
        """Clean shutdown."""
        logger.info("Shutting down...")
        if self.observer:
            self.observer.close()
        if self.reporter:
            try:
                self.reporter.generate()
            except Exception:
                logger.exception("Reporter generation failed")
        logger.info("Done. Output: %s", self.output_dir)
```

- [ ] **Step 2: Verify import**

```bash
cd /opt/quant && python3.12 -c "from live.runner import LiveRunner; print('OK')"
```

Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add live/runner.py
git commit -m "feat: add LiveRunner — unified paper/live trading loop"
```

---

### Task 2.5: CLI Entry Point

**Files:**
- Create: `live/run.py`

- [ ] **Step 1: Implement CLI**

Create `live/run.py`:

```python
#!/usr/bin/env python3.12
"""Live Loop CLI entry point.

Usage:
    python -m live.run --config live/configs/paper_us.yaml
    python -m live.run --mode paper --config live/configs/paper_us.yaml
    python -m live.run --mode live --config live/configs/live_us.yaml
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Add project root
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from live.runner import LiveRunner


def main():
    parser = argparse.ArgumentParser(description="Quant Live Trading Loop")
    parser.add_argument("--config", "-c", type=str, required=True,
                        help="Path to YAML config file")
    parser.add_argument("--mode", "-m", type=str, choices=["paper", "live"],
                        help="Override config mode")
    args = parser.parse_args()

    runner = LiveRunner(args.config)
    if args.mode:
        runner.config["live"]["mode"] = args.mode
        runner.mode = args.mode

    runner.run()


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Verify CLI help**

```bash
cd /opt/quant && python3.12 -m live.run --help
```

Expected: Usage message with --config and --mode options

- [ ] **Step 3: Commit**

```bash
git add live/run.py
git commit -m "feat: add Live Loop CLI entry point"
```

---

## Phase 3: Integration & Verification

### Task 3.1: Update MLPredStrategy to use ModelRegistry

**Files:**
- Modify: `strategies/ml_pred.py`

- [ ] **Step 1: Update MLPredStrategy**

Read current file first to determine exact edit points.

```bash
cd /opt/quant && grep -n "def _train\|def on_init\|self._model\|self._model_trainer\|factor_path\|load_from_bq\|this.train_start\|this.train_end\|model_name\|model_version" strategies/ml_pred.py
```

- [ ] **Step 2: Add model loading fields and refactor on_init**

Edit `strategies/ml_pred.py`:

Add to class fields:
```python
model_name: str = "momentum_lgbm"
model_version: int | str = "latest"
```

Replace `_train` method with `_load_model`:

```python
def _load_model(self):
    """Load trained model from ModelRegistry. Falls back to live training."""
    try:
        from ml.registry import ModelRegistry
        bundle = ModelRegistry.load(self.model_name, self.model_version)
        self._model = bundle.model
        self._config = bundle.config
        self._features = bundle.features
        logger.info("Loaded %s v%d (%d features)", 
                    bundle.name, bundle.version, len(bundle.features))
        self._trained = True
    except Exception:
        logger.exception("Failed to load model from registry, falling back to live training")
        symbols = self._get_symbols()
        self._fallback_train(symbols)

def _fallback_train(self, symbols):
    """Original training flow as fallback."""
    from ml.trainer import ModelTrainer
    trainer = ModelTrainer(factor_path=None)
    trainer.load_from_bq(symbols, self.train_start, self.train_end)
    result = trainer.train_lightgbm()
    self._model = result["model"]
    self._model_trainer = trainer
    self._trained = True
    # Don't save to registry — this is emergency fallback
```

Update `on_init`:
```python
def on_init(self, ctx):
    self._last_rebalance = -self.rebalance_every
    self._model = None
    self._trained = False
    self._all_scores_history = {}

    symbols = list(ctx.universe)
    if not symbols:
        logger.warning("MLPredStrategy: no symbols in universe")
        return

    try:
        self._load_model()  # Was: self._train(symbols)
    except Exception:
        logger.exception("MLPredStrategy training failed")
```

Also in `on_bar`, update the model reference:
```python
# Old: self._model_trainer.predict(self._model, latest)
# New: self._predict(latest)
```

Add `_predict` method:
```python
def _predict(self, df: pd.DataFrame) -> np.ndarray:
    """Predict using loaded model with proper feature handling."""
    if self._model is None:
        raise RuntimeError("Model not loaded")
    
    # Use features from bundle if available
    if hasattr(self, '_features') and self._features:
        feature_cols = [c for c in self._features if c in df.columns]
    else:
        exclude = {"symbol", "date", "fwd_ret_5d", "fwd_ret_20d"}
        feature_cols = [c for c in df.columns if c not in exclude]
    
    X = df[feature_cols].values
    X = np.nan_to_num(X, nan=0.0)
    
    if not isinstance(self._model, lgb.Booster):
        # sklearn model — need scaler
        X = self._model_trainer.scaler.transform(X) if hasattr(self._model_trainer, 'scaler') else X
    
    return self._model.predict(X)
```

- [ ] **Step 3: Run MLPredStrategy in paper mode**

```bash
cd /opt/quant && python3.12 -c "
from run_paper import PaperRunner
r = PaperRunner({
    'market':'us','capital':100000,'strategy':'strategies.ml_pred.MLPredStrategy',
    'strategy_kwargs':{'market':'us','top_k':3,'rebalance_every':5,'model_name':'momentum_lgbm','model_version':'latest'},
    'start':'2026-01-01','end':'2026-05-28',
    'symbols':['AAPL','MSFT','NVDA','AMZN','GOOGL','META','TSLA','AMD','NFLX','INTC'],
    'data_source':'bq',
})
result = r.run()
print('Return:', result['metrics']['total_return']*100, '%')
"
```

Expected: Run completes, loads model from registry, produces reasonable returns

- [ ] **Step 4: Commit**

```bash
git add strategies/ml_pred.py
git commit -m "feat: MLPredStrategy loads from ModelRegistry with fallback training"
```

---

### Task 3.2: End-to-End Paper Run

- [ ] **Step 1: Run full paper loop via CLI**

```bash
cd /opt/quant && python3.12 -m live.run --config live/configs/paper_us.yaml
```

Expected: 
- Full trading run completes
- `output/live/us_paper_*/` created with all files
- `report.html` contains equity chart, drawdown, summary
- No errors in logs

- [ ] **Step 2: Verify output files**

```bash
ls output/live/us_paper_*/
```

Expected: `config.yaml`, `trades.csv`, `signals.csv`, `positions_snapshot.csv`, `equity_curve.csv`, `alerts.log`, `report.html`

- [ ] **Step 3: Commit output config (not output data)**

No commit needed — output is gitignored. Done.

---

### Task 3.3: Live Broker Connectivity Test

- [ ] **Step 1: Test FutuStockBroker connection**

```bash
cd /opt/quant && python3.12 -c "
from oms.broker.futu_stock_broker import FutuStockBroker
import asyncio
async def test():
    broker = FutuStockBroker(host='127.0.0.1', port=11111)
    # Test market data
    price = broker.get_price('US.AAPL')
    print(f'AAPL price: {price}')
    
asyncio.run(test())
"
```

Expected: AAPL current price printed (no actual orders placed)

- [ ] **Step 2: Create live config**

Create `live/configs/live_us.yaml` (copy from paper, change mode to live):

```yaml
live:
  mode: live
  market: us
  output_dir: output/live/

broker:
  live:
    type: futu_stock
    host: 127.0.0.1
    port: 11111
    max_position_pct: 0.2

strategy:
  name: MLPredStrategy
  model_name: momentum_lgbm
  model_version: latest
  top_k: 5
  rebalance_every: 5
  fallback_strategy: SimpleMomentum

schedule:
  pre_market_warmup: 300
  bar_interval: 300
  market_close_offset: 600

risk:
  max_drawdown: 0.15
  max_daily_loss: 0.05
  position_size_pct: 0.2

observer:
  log_dir: output/live/
  snapshot_interval: 60
  trade_log: true
  equity_curve: true

dashboard:
  port: 8090
  websocket: true
```

- [ ] **Step 3: Commit**

```bash
git add live/configs/live_us.yaml
git commit -m "feat: add live trading config (Futu)"
```

---

## Plan Summary

| Phase | Tasks | Files Created | Files Modified |
|-------|-------|---------------|----------------|
| 1: ML Infra | 5 | `ml/datasets.py`, `ml/registry.py`, `ml/tuner.py`, `models/momentum_lgbm/v1/config.yaml`, `ml/tests/{test_datasets,test_registry,test_tuner}.py` | `ml/__init__.py` |
| 2: Live Loop | 5 | `live/{__init__,runner,observer,reporter,config,run}.py`, `live/configs/{paper_us,live_us}.yaml` | — |
| 3: Integration | 3 | — | `strategies/ml_pred.py` |

**Total: 13 new files, 2 modified files, 0 deleted files.**
