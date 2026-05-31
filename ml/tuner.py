"""OptunaTuner — config-driven hyperparameter tuning integrated with ModelRegistry."""
import logging
from pathlib import Path
import numpy as np
import optuna
import yaml
from optuna.samplers import TPESampler
from ml.registry import ModelRegistry, ModelBundle
from ml.datasets import DatasetManager, DatasetBundle

logger = logging.getLogger(__name__)

class OptunaTuner:
    """Hyperparameter tuning with Optuna."""

    def __init__(self, config_path: str):
        self.config_path = Path(config_path)
        with open(self.config_path) as f:
            self.config = yaml.safe_load(f)

    def tune(self, n_trials: int = None) -> ModelBundle:
        """Run Optuna study, save best model to ModelRegistry."""
        n = n_trials or self.config.get("training", {}).get("n_trials", 50)
        dataset_name = self.config.get("data", {}).get("dataset")
        if not dataset_name:
            raise ValueError("config.data.dataset is required")
        dataset = DatasetManager.load(dataset_name)
        return self._tune_with_data(dataset, n_trials=n)

    def _tune_with_data(self, dataset: DatasetBundle, n_trials: int = 50) -> ModelBundle:
        model_cfg = self.config.get("model", {})
        hp_cfg = self.config.get("hyperparams", {})
        training_cfg = self.config.get("training", {})
        data_cfg = self.config.get("data", {})
        model_name = model_cfg.get("name", "unknown")
        label = data_cfg.get("label", "fwd_ret_5d")
        direction = training_cfg.get("optuna_direction", "maximize")
        metric_name = training_cfg.get("optuna_metric", "val_ic")
        
        exclude = {"symbol", "date", label}
        feature_cols = [c for c in dataset.train.columns if c not in exclude]
        search_space = hp_cfg.get("search_space", {})
        fixed_params = hp_cfg.get("fixed", {})

        def objective(trial):
            params = {**fixed_params}
            for name, spec in search_space.items():
                ptype = spec["type"]
                low, high = spec["low"], spec["high"]
                if ptype == "int":
                    params[name] = trial.suggest_int(name, low, high)
                elif ptype == "loguniform":
                    params[name] = trial.suggest_float(name, low, high, log=True)
                elif ptype == "uniform":
                    params[name] = trial.suggest_float(name, low, high)
            
            import lightgbm as lgb
            # Get clean train/val data (drop NaN in features + label)
            t = dataset.train.dropna(subset=feature_cols + [label])
            v = dataset.val.dropna(subset=feature_cols + [label])
            if len(t) == 0 or len(v) == 0:
                return float("nan")
            train_X = t[feature_cols].values
            train_y = t[label].values
            val_X = v[feature_cols].values
            val_y = v[label].values
            
            train_data = lgb.Dataset(train_X, label=train_y)
            val_data = lgb.Dataset(val_X, label=val_y, reference=train_data)
            model = lgb.train(params, train_data, valid_sets=[val_data],
                             callbacks=[lgb.early_stopping(10), lgb.log_evaluation(0)])
            preds = model.predict(val_X)
            
            if metric_name == "val_ic":
                from scipy.stats import spearmanr
                ic, _ = spearmanr(preds, val_y)
                return float(ic)
            else:
                from sklearn.metrics import mean_squared_error
                return float(np.sqrt(mean_squared_error(val_y, preds)))

        study = optuna.create_study(
            direction="maximize" if direction == "maximize" else "minimize",
            sampler=TPESampler(seed=42),
        )
        study.optimize(objective, n_trials=n_trials, show_progress_bar=True)
        
        # Train best model on clean data
        best_params = {**fixed_params, **study.best_params}
        logger.info("Best params: %s", study.best_params)
        logger.info("Best %s: %.4f", metric_name, study.best_value)
        import lightgbm as lgb
        t = dataset.train.dropna(subset=feature_cols + [label])
        v = dataset.val.dropna(subset=feature_cols + [label])
        ts = dataset.test.dropna(subset=feature_cols + [label])
        train_X = t[feature_cols].values
        train_y = t[label].values
        val_X = v[feature_cols].values
        val_y = v[label].values
        test_X = ts[feature_cols].values
        test_y = ts[label].values
        
        train_data = lgb.Dataset(train_X, label=train_y)
        val_data = lgb.Dataset(val_X, label=val_y, reference=train_data)
        best_model = lgb.train(best_params, train_data, valid_sets=[val_data])
        
        # Evaluate
        from scipy.stats import spearmanr
        from sklearn.metrics import mean_squared_error
        val_preds = best_model.predict(val_X)
        val_ic, _ = spearmanr(val_preds, val_y)
        test_preds = best_model.predict(test_X)
        test_ic, _ = spearmanr(test_preds, test_y)
        train_preds = best_model.predict(train_X)
        
        metrics = {
            "val_ic": float(val_ic), "val_rmse": float(np.sqrt(mean_squared_error(val_y, val_preds))),
            "test_ic": float(test_ic), "test_rmse": float(np.sqrt(mean_squared_error(test_y, test_preds))),
            "train_rmse": float(np.sqrt(mean_squared_error(train_y, train_preds))),
            "optuna_best_value": float(study.best_value), "n_trials": n_trials,
        }
        
        dataset_name = self.config.get("data", {}).get("dataset", "")
        version = ModelRegistry.save(name=model_name, model=best_model, config=self.config,
                                     metrics=metrics, features=feature_cols, dataset_name=dataset_name)
        ModelRegistry.promote(model_name, version, "production")
        return ModelRegistry.load(model_name, version)
