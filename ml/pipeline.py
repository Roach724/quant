"""TrainPipeline — unified ML workflow from config YAML."""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

import pandas as pd
import yaml
from google.cloud import bigquery

from ml.trainer import ModelTrainer
from ml.registry import ModelRegistry

logger = logging.getLogger(__name__)


class TrainPipeline:
    """Config-driven training pipeline.

    Reads a YAML config → loads dataset from BQ → trains model → evaluates → registers.

    Usage:
        p = TrainPipeline("ml/configs/lgb_us_v1.yaml")
        result = p.run(skip_tuning=False)
    """

    def __init__(self, config_path: str):
        self.config_path = Path(config_path)
        with open(self.config_path) as f:
            self.config = yaml.safe_load(f)

    def run(self, skip_tuning: bool = False) -> dict[str, Any]:
        """Execute full pipeline. Returns result dict."""
        t0 = time.time()
        data_cfg = self.config.get("data", {})
        model_cfg = self.config.get("model", {})
        tuning_cfg = self.config.get("tuning", {})
        eval_cfg = self.config.get("evaluation", {})
        registry_cfg = self.config.get("registry", {})

        dataset_name = data_cfg.get("dataset", "")
        if not dataset_name:
            return {"error": "config.data.dataset is required"}

        # ── 1. Load dataset from BQ ──────────────────────────────────
        logger.info("Loading dataset '%s' from BQ...", dataset_name)
        client = bigquery.Client(project="deductive-notch-495015-c2")
        table = self._resolve_dataset_table(client, dataset_name)
        label = data_cfg.get("label", "fwd_ret_5d")

        df = self._load_dataset(client, table)
        if df.empty:
            return {"error": f"Dataset '{dataset_name}' is empty"}

        # Split by split column
        train_df = df[df["split"] == "train"].copy()
        val_df = df[df["split"] == "val"].copy()
        test_df = df[df["split"] == "test"].copy()

        if train_df.empty:
            return {"error": "Train split is empty"}

        # Identify feature columns
        exclude = {"symbol", "date", "split", "timestamp", label, "fwd_ret_20d"}
        feature_cols = [c for c in df.columns if c not in exclude]
        logger.info("Data: train=%d, val=%d, test=%d, features=%d",
                     len(train_df), len(val_df), len(test_df), len(feature_cols))

        # ── 2. Train model ──────────────────────────────────────────
        model_type = model_cfg.get("type", "lightgbm")
        params = model_cfg.get("params", {})

        if tuning_cfg.get("enabled") and not skip_tuning:
            logger.info("Tuning enabled — running Optuna...")
            best_params = self._run_tuning(
                train_df, val_df, feature_cols, label,
                params, tuning_cfg,
            )
            params = {**params, **best_params}

        trainer = ModelTrainer(factor_path=None)
        trainer.factor_df = df  # set data directly
        trainer.feature_cols = feature_cols
        trainer.label_col = label

        if model_type == "lightgbm":
            result = trainer.train_lightgbm(train_df, val_df)
        elif model_type in ("ridge", "ols"):
            # For linear models, need to fit scaler on train data
            from sklearn.preprocessing import StandardScaler
            trainer.scaler = StandardScaler()
            X_train = trainer._prepare_xy(train_df, fit_scaler=True)[0] if model_type == "ridge" else None
            if model_type == "ridge":
                alpha = float(model_cfg.get("params", {}).get("alpha", 1.0))
                result = trainer.train_ridge(train_df, val_df, alpha=alpha)
            else:
                result = trainer.train_ols(train_df, val_df)
        else:
            return {"error": f"Unknown model type: {model_type}"}

        model = result["model"]

        # ── 3. Evaluate ────────────────────────────────────────────
        overall_ic = None
        icir = None
        if "rank_ic" in eval_cfg.get("metrics", ["rank_ic"]):
            ic_result = trainer.evaluate_ic(model, test_df, name=model_type)
            overall_ic = ic_result["overall_rank_ic"]
            icir = ic_result["icir"]

        # ── 4. Register to MLflow ──────────────────────────────────
        model_name = registry_cfg.get("model_name", dataset_name)
        metrics = {
            "rmse": result.get("rmse_val", result.get("rmse", 0)),
            "rank_ic": float(overall_ic) if overall_ic else 0,
            "icir": float(icir) if icir else 0,
            "n_features": len(feature_cols),
            "train_rows": len(train_df),
            "test_rows": len(test_df),
            "best_iteration": result.get("best_iteration", 0),
        }
        version = ModelRegistry.save(
            name=model_name, model=model,
            config=self.config, metrics=metrics,
            features=feature_cols, dataset_name=dataset_name,
        )
        ModelRegistry.promote(model_name, version, "production")
        logger.info("Registered %s v%d to MLflow", model_name, version)

        duration = time.time() - t0
        return {
            "model_name": model_name,
            "version": version,
            "metrics": metrics,
            "features": feature_cols[:20],
            "duration_sec": round(duration, 1),
        }

    # ── Internal ────────────────────────────────────────────────────

    def _resolve_dataset_table(self, client, name: str) -> str:
        """Look up BQ table name from ml_datasets registry, or default."""
        try:
            rows = client.query(
                "SELECT bq_table FROM admin.ml_datasets WHERE name=@name",
                job_config=bigquery.QueryJobConfig(
                    query_parameters=[bigquery.ScalarQueryParameter("name", "STRING", name)]
                ),
            ).result()
            for r in rows:
                if r.bq_table:
                    return r.bq_table
        except Exception:
            pass
        return f"deductive-notch-495015-c2.ml_dataset.{name}"

    def _load_dataset(self, client, table: str) -> pd.DataFrame:
        """Load dataset from BQ table."""
        try:
            return client.query(f"SELECT * FROM {table}").to_dataframe()
        except Exception:
            logger.warning("Table %s not found, trying project-less lookup", table)
            return pd.DataFrame()

    def _run_tuning(self, train_df, val_df, feature_cols, label, base_params, tuning_cfg):
        """Run Optuna hyperparameter tuning."""
        import optuna
        from optuna.samplers import TPESampler
        import lightgbm as lgb
        from scipy.stats import spearmanr

        n_trials = tuning_cfg.get("n_trials", 50)
        direction = tuning_cfg.get("direction", "maximize")
        metric_name = tuning_cfg.get("metric", "val_ic")
        search_space = tuning_cfg.get("search_space", {})

        t = train_df.dropna(subset=feature_cols + [label])
        v = val_df.dropna(subset=feature_cols + [label])
        train_X = t[feature_cols].values
        train_y = t[label].values
        val_X = v[feature_cols].values
        val_y = v[label].values

        def objective(trial):
            p = dict(base_params)
            for name, spec in search_space.items():
                ptype = spec["type"]
                low, high = spec["low"], spec["high"]
                if ptype == "int":
                    p[name] = trial.suggest_int(name, low, high)
                elif ptype == "loguniform":
                    p[name] = trial.suggest_float(name, low, high, log=True)
                elif ptype == "uniform":
                    p[name] = trial.suggest_float(name, low, high)

            p.setdefault("objective", "regression")
            p.setdefault("metric", "rmse")
            p.setdefault("verbose", -1)
            p.setdefault("seed", 42)
            num_boost = p.pop("num_boost_round", 2000)
            early_stop = p.pop("early_stopping_rounds", 100)

            dtrain = lgb.Dataset(train_X, label=train_y)
            dval = lgb.Dataset(val_X, label=val_y, reference=dtrain)
            model = lgb.train(p, dtrain, num_boost_round=num_boost,
                              valid_sets=[dval],
                              callbacks=[lgb.early_stopping(early_stop), lgb.log_evaluation(0)])

            preds = model.predict(val_X)
            if metric_name == "val_ic":
                ic, _ = spearmanr(preds, val_y)
                return float(ic)
            else:
                from sklearn.metrics import mean_squared_error
                return float(-1 * np.sqrt(mean_squared_error(val_y, preds)))

        study = optuna.create_study(
            direction="maximize" if direction == "maximize" else "minimize",
            sampler=TPESampler(seed=42),
        )
        study.optimize(objective, n_trials=n_trials, show_progress_bar=False)
        logger.info("Best params: %s, best %s: %.4f", study.best_params, metric_name, study.best_value)
        return study.best_params
