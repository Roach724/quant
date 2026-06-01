"""Retrain us_tech v1 — explicit 39 tech factors, Optuna 20 trials."""
import logging, sys, os
sys.path.insert(0, "/opt/quant")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s [%(name)s] %(message)s")
logger = logging.getLogger("train")

import numpy as np
import pandas as pd
import optuna
from optuna.samplers import TPESampler
from google.cloud import bigquery
from ml.trainer import ModelTrainer
from ml.registry import ModelRegistry

MODEL_NAME = "us_tech"
MODEL_VERSION = 1
LABEL = "fwd_ret_5d"
N_TRIALS = 20

def safe_to_numpy(df, cols):
    """Convert DataFrame columns to numpy, handling pd.NA and inf."""
    X = df[cols].copy()
    for c in cols:
        X[c] = pd.to_numeric(X[c], errors='coerce')
    X = X.fillna(0.0).replace([np.inf, -np.inf], 0.0)
    return X.values.astype(np.float64)

def main():
    bq = bigquery.Client(project="deductive-notch-495015-c2")
    symbols = bq.query(
        "SELECT DISTINCT symbol FROM quant.factor_values WHERE source_builder='tech' ORDER BY symbol"
    ).result().to_dataframe()["symbol"].tolist()
    logger.info("Symbols: %d", len(symbols))

    rows = bq.query(
        "SELECT DISTINCT factor_id FROM quant.factor_values WHERE source_builder='tech' ORDER BY factor_id"
    ).result()
    all_factor_ids = [r.factor_id for r in rows]
    logger.info("Tech factors: %d", len(all_factor_ids))

    trainer = ModelTrainer(factor_path=None)
    df = trainer.load_from_bq(
        symbols=symbols, market="us", start="2020-01-01", end="2025-12-31",
        factor_ids=all_factor_ids,
    )
    if df.empty:
        logger.error("No data"); return 1

    logger.info("Data: %d rows × %d cols", len(df), len(df.columns))

    df = df.sort_values("date").reset_index(drop=True)
    split_idx = int(len(df) * 0.8)
    train_df, val_df = df.iloc[:split_idx], df.iloc[split_idx:]

    exclude = {"symbol", "date", "fwd_ret_5d", "fwd_ret_20d"}
    feature_cols = [c for c in df.columns if c not in exclude]
    logger.info("Features: %d, Train: %d, Val: %d", len(feature_cols), len(train_df), len(val_df))

    X_train = safe_to_numpy(train_df, feature_cols)
    y_train = safe_to_numpy(train_df, [LABEL]).ravel()
    X_val = safe_to_numpy(val_df, feature_cols)
    y_val = safe_to_numpy(val_df, [LABEL]).ravel()

    def objective(trial):
        params = {
            "objective": "regression", "metric": "rmse", "verbosity": -1, "boosting_type": "gbdt",
            "num_leaves": trial.suggest_int("num_leaves", 16, 128),
            "max_depth": trial.suggest_int("max_depth", 3, 12),
            "learning_rate": trial.suggest_float("learning_rate", 0.01, 0.3, log=True),
            "n_estimators": trial.suggest_int("n_estimators", 50, 500),
            "subsample": trial.suggest_float("subsample", 0.5, 1.0),
            "colsample_bytree": trial.suggest_float("colsample_bytree", 0.5, 1.0),
            "reg_alpha": trial.suggest_float("reg_alpha", 1e-8, 10.0, log=True),
            "reg_lambda": trial.suggest_float("reg_lambda", 1e-8, 10.0, log=True),
            "min_child_samples": trial.suggest_int("min_child_samples", 5, 100),
        }
        import lightgbm as lgb
        model = lgb.LGBMRegressor(**params, n_jobs=-1, random_state=42)
        model.fit(X_train, y_train, eval_set=[(X_val, y_val)], eval_metric="rmse",
                  callbacks=[lgb.early_stopping(50), lgb.log_evaluation(0)])
        preds = model.predict(X_val)
        rmse = float(np.sqrt(np.mean((preds - y_val)**2)))
        from scipy.stats import spearmanr
        try:
            ic, _ = spearmanr(preds, y_val)
            if np.isnan(ic): ic = 0.0
        except: ic = 0.0
        trial.set_user_attr("ic", float(ic))
        return rmse

    logger.info("Optuna %d trials", N_TRIALS)
    study = optuna.create_study(direction="minimize", sampler=TPESampler(seed=42))
    study.optimize(objective, n_trials=N_TRIALS, show_progress_bar=True)

    best = study.best_trial
    logger.info("Best #%d: RMSE=%.6f IC=%.4f", best.number, best.value, best.user_attrs["ic"])

    import lightgbm as lgb
    final = lgb.LGBMRegressor(**best.params, n_jobs=-1, random_state=42)
    final.fit(X_train, y_train, eval_set=[(X_val, y_val)], eval_metric="rmse",
              callbacks=[lgb.early_stopping(50), lgb.log_evaluation(0)])
    final_preds = final.predict(X_val)
    final_rmse = float(np.sqrt(np.mean((final_preds - y_val)**2)))
    from scipy.stats import spearmanr
    try:
        final_ic, _ = spearmanr(final_preds, y_val)
        if np.isnan(final_ic): final_ic = 0.0
    except: final_ic = 0.0
    logger.info("Final: RMSE=%.6f IC=%.4f", final_rmse, final_ic)

    if hasattr(final, 'feature_importances_'):
        importances = sorted(zip(feature_cols, final.feature_importances_), key=lambda x: -x[1])
        logger.info("Top 10 features:")
        for name, imp in importances[:10]:
            logger.info("  %-25s %.1f", name, imp)

    config = {
        "model": {"type": "lightgbm", "name": MODEL_NAME},
        "factors": {"source": "tech", "mode": "explicit", "n_factors": len(all_factor_ids)},
        "data": {"label": LABEL, "market": "us"},
        "training": {"n_trials": N_TRIALS, "n_train": len(train_df), "n_val": len(val_df)},
    }
    metrics = {"rmse": final_rmse, "ic": float(final_ic)}

    version = ModelRegistry.save(
        name=MODEL_NAME, model=final, config=config, metrics=metrics,
        features=feature_cols, dataset_name="us_tech_v1",
    )
    logger.info("✅ Registered: %s v%d (RMSE=%.4f, IC=%.4f, %d features)",
                MODEL_NAME, version, final_rmse, final_ic, len(feature_cols))

    bundle = ModelRegistry.load(MODEL_NAME, MODEL_VERSION)
    logger.info("✅ Verified: %s v%d, %d features",
                bundle.name, bundle.version, len(bundle.features))
    return 0

if __name__ == "__main__":
    sys.exit(main())
