"""
ModelRegistry — MLflow Tracking + Model Registry wrapper.

Provides:
    ModelBundle — Dataclass for loaded model + config + features + metadata
    ModelRegistry — save/load/list/promote models via MLflow

Backend: SQLite at $HOME/.mlflow/mlflow.db
Artifacts: /opt/quant/models_artifacts/
"""

from __future__ import annotations

import logging
import os
import subprocess
import tempfile
from dataclasses import dataclass, field
from typing import ClassVar

import yaml

import mlflow
import mlflow.lightgbm
from mlflow.tracking import MlflowClient

logger = logging.getLogger(__name__)


# ── Dataclass ───────────────────────────────────────────────────────────────


@dataclass
class ModelBundle:
    """Loaded model with metadata."""

    name: str
    version: int
    model: object  # lgb.Booster or sklearn estimator or pyfunc wrapper
    config: dict
    features: list[str]
    metadata: dict = field(default_factory=dict)


# ── ModelRegistry ───────────────────────────────────────────────────────────


class ModelRegistry:
    """Unified MLflow interface for model lifecycle management.

    All methods are classmethods — no instantiation needed.
    """

    _initialized: ClassVar[bool] = False
    _ARTIFACT_ROOT: ClassVar[str] = "/opt/quant-prod/models_artifacts"

    @classmethod
    def _tracking_uri(cls) -> str:
        return "http://127.0.0.1:5000"

    # ── Init ────────────────────────────────────────────────────────────

    @classmethod
    def _init(cls) -> None:
        """Lazy-init MLflow tracking URI, artifact root, and experiment."""
        if cls._initialized:
            return

        os.makedirs(cls._ARTIFACT_ROOT, exist_ok=True)
        mlflow.set_tracking_uri(cls._tracking_uri())

        try:
            mlflow.create_experiment("quant", artifact_location=cls._ARTIFACT_ROOT)
        except Exception:
            pass  # experiment already exists

        mlflow.set_experiment("quant")
        cls._initialized = True
        logger.info("ModelRegistry initialised: %s", cls._tracking_uri())

    # ── Helpers ─────────────────────────────────────────────────────────

    @staticmethod
    def _git_commit() -> str | None:
        """Return short git commit hash or None."""
        try:
            return subprocess.check_output(
                ["git", "rev-parse", "--short", "HEAD"],
                text=True,
                cwd="/opt/quant",
                timeout=5,
            ).strip()
        except Exception:
            return None

    @classmethod
    def _client(cls) -> MlflowClient:
        cls._init()
        return MlflowClient()

    # ── Save ────────────────────────────────────────────────────────────

    @classmethod
    def save(
        cls,
        name: str,
        model: object,
        config: dict,
        metrics: dict,
        features: list[str],
        dataset_name: str,
        artifacts: dict | None = None,
    ) -> int:
        """Save a model to the MLflow registry.

        Args:
            name: Registered model name.
            model: Trained model (LightGBM Booster, sklearn estimator, or other).
            config: Model config dict with keys ``model``, ``factors``, ``data``.
            metrics: Dict of ``metric_name → float``.
            features: List of feature column names.
            dataset_name: Training dataset identifier.
            artifacts: Optional dict of ``name → local_path`` for extra artifacts.

        Returns:
            Registered model version number (int).
        """
        cls._init()

        model_type = config.get("model", {}).get("type", "lightgbm")
        label = config.get("data", {}).get("label", "")

        with mlflow.start_run() as run:
            # ── Params ──
            mlflow.log_param("model_name", name)
            mlflow.log_param("model_type", model_type)
            mlflow.log_param("dataset", dataset_name)
            mlflow.log_param("n_features", len(features))
            if label:
                mlflow.log_param("label", label)

            commit = cls._git_commit()
            if commit:
                mlflow.log_param("git_commit", commit)

            factor_cfg = config.get("factors", {})
            for k in ("source", "top_n"):
                if k in factor_cfg:
                    mlflow.log_param(f"factor_{k}", str(factor_cfg[k]))

            # ── Features as text ──
            mlflow.log_text("\n".join(features), "features.txt")

            # ── Metrics ──
            for k, v in metrics.items():
                mlflow.log_metric(k, v)

            # ── Config as YAML artifact ──
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".yaml", delete=False
            ) as f:
                yaml.dump(config, f)
                config_path = f.name
            mlflow.log_artifact(config_path, "config")
            os.unlink(config_path)

            # ── Extra artifacts ──
            if artifacts:
                for art_name, art_path in artifacts.items():
                    mlflow.log_artifact(art_path, f"extra/{art_name}")

            # ── Log model ──
            if model_type == "lightgbm":
                mlflow.lightgbm.log_model(model, "model")
            else:
                # Non-lightgbm: save via joblib, wrap as pyfunc for registration
                import joblib

                with tempfile.NamedTemporaryFile(
                    suffix=".joblib", delete=False
                ) as tmp:
                    joblib.dump(model, tmp.name)
                    model_path = tmp.name

                class _CustomPyfunc(mlflow.pyfunc.PythonModel):
                    def load_context(self, context):
                        import joblib as _jl
                        self.model = _jl.load(context.artifacts["model_file"])

                    def predict(self, context, model_input, params=None):
                        return self.model.predict(model_input)

                mlflow.pyfunc.log_model(
                    artifact_path="model",
                    python_model=_CustomPyfunc(),
                    artifacts={"model_file": model_path},
                )
                os.unlink(model_path)

            # ── Register model ──
            result = mlflow.register_model(
                f"runs:/{run.info.run_id}/model", name
            )

            # Auto-promote to production
            client = MlflowClient()
            try:
                client.transition_model_version_stage(
                    name=name, version=result.version, stage="production"
                )
            except Exception:
                pass

            version = int(result.version)
            logger.info(
                "Saved model '%s' v%d (type=%s, features=%d, dataset=%s)",
                name, version, model_type, len(features), dataset_name,
            )
            return version

    # ── Load ────────────────────────────────────────────────────────────

    @classmethod
    def load(
        cls, name: str, version: str | int = "latest"
    ) -> ModelBundle:
        """Load a registered model and its metadata.

        Args:
            name: Registered model name.
            version: Version number (int) or ``"latest"`` (default).

        Returns:
            ``ModelBundle`` with model object, config, features, and metadata.

        Raises:
            ValueError: If model or version is not found.
        """
        client = cls._client()

        # Resolve version
        version_int = cls._resolve_version(client, name, version)

        # Get model version info
        mv = client.get_model_version(name, str(version_int))
        run_id = mv.run_id
        run_data = client.get_run(run_id)
        params = run_data.data.params
        model_type = params.get("model_type", "lightgbm")

        # Load model object
        model_uri = f"models:/{name}/{version_int}"
        if model_type == "lightgbm":
            model_obj = mlflow.lightgbm.load_model(model_uri)
        else:
            model_obj = mlflow.pyfunc.load_model(model_uri)

        # Download artifacts
        config = {}
        try:
            config_local = client.download_artifacts(run_id, "config/config.yaml")
            with open(config_local) as f:
                config = yaml.safe_load(f)
        except Exception:
            logger.warning("Could not load config.yaml for '%s' v%d", name, version_int)

        features: list[str] = []
        try:
            features_local = client.download_artifacts(run_id, "features.txt")
            with open(features_local) as f:
                features = [line.strip() for line in f if line.strip()]
        except Exception:
            logger.warning("Could not load features.txt for '%s' v%d", name, version_int)

        metadata = {
            "run_id": run_id,
            "created_at": mv.creation_timestamp,
            "stage": mv.current_stage.lower() if mv.current_stage else None,
            "dataset": params.get("dataset"),
            "git_commit": params.get("git_commit"),
            "model_type": model_type,
        }

        logger.info(
            "Loaded model '%s' v%d (type=%s, features=%d)",
            name, version_int, model_type, len(features),
        )
        return ModelBundle(
            name=name,
            version=version_int,
            model=model_obj,
            config=config,
            features=features,
            metadata=metadata,
        )

    # ── List versions ───────────────────────────────────────────────────

    @classmethod
    def list_versions(cls, name: str) -> list[dict]:
        """List all registered versions of a model.

        Returns:
            List of dicts with keys: ``version``, ``stage``, ``created_at``,
            ``metrics``, ``dataset``.
        """
        client = cls._client()

        try:
            model_versions = client.search_model_versions(f"name='{name}'")
        except Exception:
            return []

        results: list[dict] = []
        for mv in model_versions:
            entry = {
                "version": int(mv.version),
                "stage": mv.current_stage.lower() if mv.current_stage else None,
                "created_at": mv.creation_timestamp,
                "dataset": None,
                "metrics": {},
            }
            # Enrich with run data
            if mv.run_id:
                try:
                    rd = client.get_run(mv.run_id)
                    entry["dataset"] = rd.data.params.get("dataset")
                    entry["metrics"] = dict(rd.data.metrics)
                except Exception:
                    pass
            results.append(entry)

        results.sort(key=lambda v: v["version"], reverse=True)
        return results

    # ── Promote ─────────────────────────────────────────────────────────

    @classmethod
    def promote(cls, name: str, version: int, stage: str) -> None:
        """Transition a model version to a new stage.

        Args:
            name: Registered model name.
            version: Model version number.
            stage: Target stage (``"staging"``, ``"production"``, ``"archived"``).
        """
        client = cls._client()
        client.transition_model_version_stage(
            name=name, version=str(version), stage=stage,
        )
        logger.info("Promoted '%s' v%d → %s", name, version, stage)

    # ── Get latest ──────────────────────────────────────────────────────

    @classmethod
    def get_latest_version(
        cls, name: str, stage: str = "production"
    ) -> int | None:
        """Get the latest version number in a given stage.

        Returns:
            Version number or ``None`` if no matching version exists.
        """
        versions = cls.list_versions(name)
        for v in versions:
            if (v.get("stage") or "").lower() == stage.lower():
                return v["version"]
        return None

    # ── Internal ────────────────────────────────────────────────────────

    @classmethod
    def _resolve_version(
        cls,
        client: MlflowClient,
        name: str,
        version: str | int,
    ) -> int:
        """Resolve version to an integer."""
        if version == "latest" or version is None:
            versions = client.search_model_versions(f"name='{name}'")
            if not versions:
                raise ValueError(f"No versions found for model '{name}'")
            return max(int(v.version) for v in versions)
        return int(version)
