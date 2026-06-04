"""Train hk_tech v1 — direct from BQ factor_values, Optuna 20 trials.

Much faster than computing factors on-the-fly from bars,
since factor_values already has 270 HK symbols × 39 tech factors pre-computed.
"""
import logging, sys, os
sys.path.insert(0, "/opt/quant-prod")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s [%(name)s] %(message)s")
logger = logging.getLogger("train_hk_tech")

import numpy as np
import pandas as pd
import optuna
from optuna.samplers import TPESampler
from google.cloud import bigquery
from scipy.stats import spearmanr

MODEL_NAME = "hk_tech"
LABEL = "fwd_ret_5d"
N_TRIALS = 20
PROJECT = "deductive-notch-495015-c2"


def load_hk_data():
    """Load tech factors from factor_values (wide pivot) + labels from bars."""
    bq = bigquery.Client(project=PROJECT)

    # ── Step 1: Get all tech factor_ids for HK ──
    fids_df = bq.query(
        f"SELECT DISTINCT factor_id FROM `{PROJECT}.quant.factor_values` "
        "WHERE source_builder='tech' AND STARTS_WITH(symbol, 'HK.') "
        "ORDER BY factor_id"
    ).result().to_dataframe()
    all_factor_ids = fids_df["factor_id"].tolist()
    logger.info("Factor IDs: %d", len(all_factor_ids))

    # ── Step 2: Load factor values, pivot to wide ──
    logger.info("Loading factor_values for HK tech...")
    fv_df = bq.query(
        f"SELECT symbol, date, factor_id, value "
        f"FROM `{PROJECT}.quant.factor_values` "
        "WHERE source_builder='tech' AND STARTS_WITH(symbol, 'HK.') "
        "ORDER BY symbol, date, factor_id"
    ).result().to_dataframe()
    logger.info("Factor values loaded: %d rows", len(fv_df))

    # Pivot: symbol + date as index, factor_id as columns
    factors_wide = fv_df.pivot_table(
        index=["symbol", "date"], columns="factor_id", values="value"
    ).reset_index()
    # Rename columns: hk_ret_5d → ret_5d
    factors_wide.columns = [
        c.replace("hk_", "", 1) if isinstance(c, str) else c
        for c in factors_wide.columns
    ]
    logger.info("Wide factors: %d rows × %d cols", len(factors_wide), len(factors_wide.columns))

    # ── Step 3: Compute labels from bars ──
    logger.info("Loading close prices for labels...")
    bars_df = bq.query(
        f"SELECT symbol, timestamp AS date, close "
        f"FROM `{PROJECT}.quant.hk_bars_1d` "
        "WHERE STARTS_WITH(symbol, 'HK.') "
        "ORDER BY symbol, timestamp"
    ).result().to_dataframe()
    bars_df["date"] = pd.to_datetime(bars_df["date"]).dt.date
    bars_df = bars_df.drop_duplicates(subset=["symbol", "date"])
    logger.info("Bars: %d rows, %d symbols", len(bars_df), bars_df["symbol"].nunique())

    # Compute fwd_ret_5d per symbol
    labels = []
    for sym, g in bars_df.groupby("symbol"):
        g = g.sort_values("date").reset_index(drop=True)
        g["fwd_close"] = g["close"].shift(-5)
        g["fwd_ret_5d"] = (g["fwd_close"] - g["close"]) / g["close"]
        g = g.dropna(subset=["fwd_ret_5d"])
        labels.append(g[["symbol", "date", "fwd_ret_5d"]])
    labels_df = pd.concat(labels, ignore_index=True)
    logger.info("Labels: %d rows", len(labels_df))

    # ── Step 4: Merge ──
    result = factors_wide.merge(labels_df, on=["symbol", "date"], how="inner")
    logger.info("Merged: %d rows × %d cols, %d symbols",
                len(result), len(result.columns), result["symbol"].nunique())

    return result


def safe_to_numpy(df, cols):
    """Convert DataFrame columns to numpy, handling pd.NA and inf."""
    X = df[cols].copy()
    for c in cols:
        X[c] = pd.to_numeric(X[c], errors='coerce')
    X = X.fillna(0.0).replace([np.inf, -np.inf], 0.0)
    return X.values.astype(np.float64)


def main():
    # ── Load data ──
    df = load_hk_data()
    if df.empty:
        logger.error("No data loaded"); return 1

    df = df.sort_values("date").reset_index(drop=True)

    # ── Split: 80/20 chronological ──
    split_idx = int(len(df) * 0.8)
    train_df, val_df = df.iloc[:split_idx], df.iloc[split_idx:]

    # Feature columns (exclude metadata + labels)
    exclude = {"symbol", "date", "fwd_ret_5d", "fwd_ret_20d"}
    feature_cols = [c for c in df.columns if c not in exclude]
    logger.info("Features: %d, Train: %d rows, Val: %d rows",
                len(feature_cols), len(train_df), len(val_df))

    X_train = safe_to_numpy(train_df, feature_cols)
    y_train = safe_to_numpy(train_df, [LABEL]).ravel()
    X_val = safe_to_numpy(val_df, feature_cols)
    y_val = safe_to_numpy(val_df, [LABEL]).ravel()

    # ── Optuna objective ──
    def objective(trial):
        params = {
            "objective": "regression", "metric": "rmse", "verbosity": -1,
            "boosting_type": "gbdt",
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
        model.fit(X_train, y_train,
                  eval_set=[(X_val, y_val)],
                  eval_metric="rmse",
                  callbacks=[lgb.early_stopping(50), lgb.log_evaluation(0)])
        preds = model.predict(X_val)
        rmse = float(np.sqrt(np.mean((preds - y_val) ** 2)))
        try:
            ic, _ = spearmanr(preds, y_val)
            if np.isnan(ic): ic = 0.0
        except Exception:
            ic = 0.0
        trial.set_user_attr("ic", float(ic))
        return rmse

    # ── Tune ──
    logger.info("Optuna %d trials starting...", N_TRIALS)
    study = optuna.create_study(
        direction="minimize",
        sampler=TPESampler(seed=42),
    )
    study.optimize(objective, n_trials=N_TRIALS, show_progress_bar=True)

    best = study.best_trial
    logger.info("Best trial #%d: RMSE=%.6f  IC=%.4f",
                best.number, best.value, best.user_attrs["ic"])
    logger.info("Best params: %s", best.params)

    # ── Final model ──
    import lightgbm as lgb
    final = lgb.LGBMRegressor(**best.params, n_jobs=-1, random_state=42)
    final.fit(X_train, y_train,
              eval_set=[(X_val, y_val)],
              eval_metric="rmse",
              callbacks=[lgb.early_stopping(50), lgb.log_evaluation(0)])

    final_preds = final.predict(X_val)
    final_rmse = float(np.sqrt(np.mean((final_preds - y_val) ** 2)))
    try:
        final_ic, _ = spearmanr(final_preds, y_val)
        if np.isnan(final_ic): final_ic = 0.0
    except Exception:
        final_ic = 0.0
    logger.info("Final model: RMSE=%.6f  IC=%.4f", final_rmse, final_ic)

    # Feature importance
    if hasattr(final, 'feature_importances_'):
        importances = sorted(
            zip(feature_cols, final.feature_importances_),
            key=lambda x: -x[1]
        )
        logger.info("Top 15 features:")
        for name, imp in importances[:15]:
            logger.info("  %-25s %.1f", name, imp)

    # ── Register ──
    from ml.registry import ModelRegistry

    config = {
        "model": {"type": "lightgbm", "name": MODEL_NAME},
        "factors": {
            "source": "tech",
            "mode": "bq_factor_values",
            "n_factors": len(feature_cols),
        },
        "data": {
            "label": LABEL,
            "market": "hk",
            "n_symbols": df["symbol"].nunique(),
        },
        "training": {
            "n_trials": N_TRIALS,
            "n_train": len(train_df),
            "n_val": len(val_df),
            "optuna_direction": "minimize",
        },
    }
    metrics = {
        "rmse": final_rmse,
        "ic": float(final_ic),
    }

    version = ModelRegistry.save(
        name=MODEL_NAME,
        model=final,
        config=config,
        metrics=metrics,
        features=feature_cols,
        dataset_name="hk_tech_v1",
    )
    logger.info("✅ Registered: %s v%d (RMSE=%.4f, IC=%.4f, %d features)",
                MODEL_NAME, version, final_rmse, final_ic, len(feature_cols))

    # ── Verify ──
    bundle = ModelRegistry.load(MODEL_NAME, "latest")
    logger.info("✅ Verified load: %s v%d, %d features",
                bundle.name, bundle.version, len(bundle.features))

    return 0


if __name__ == "__main__":
    sys.exit(main())
