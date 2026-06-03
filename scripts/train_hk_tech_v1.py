"""Train hk_tech v1 — explicit 39 tech factors, Optuna 20 trials, ModelRegistry.

Loads OHLCV from bars table, computes factors on-the-fly via TechFactorBuilder
(same approach as us_tech_v1_explicit), because hk factor_values only has 2 days
of pre-computed data.
"""
import logging, sys, os
sys.path.insert(0, "/opt/quant-dev")
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
BARS_TABLE = f"{PROJECT}.quant.hk_bars_1d"


def load_hk_data():
    """Load HK bars, compute all 39 tech factors + labels on-the-fly."""
    from factors.tech_builder import TechFactorBuilder

    bq = bigquery.Client(project=PROJECT)

    # ── Step 1: Get all HK symbols from bars ──
    syms_df = bq.query(
        f"SELECT DISTINCT symbol FROM `{BARS_TABLE}` ORDER BY symbol"
    ).result().to_dataframe()
    raw_symbols = syms_df["symbol"].tolist()
    logger.info("Raw HK symbols from bars: %d", len(raw_symbols))

    # Normalize: strip HK. prefix, strip leading zeros
    normalized = []
    for s in raw_symbols:
        s_norm = s.replace("HK.", "").lstrip("0")
        if s_norm not in normalized:
            normalized.append(s_norm)
    logger.info("Unique normalized HK symbols: %d", len(normalized))

    # ── Step 2: Load all OHLCV data ──
    # Query with all raw symbol variants to catch everything
    logger.info("Loading OHLCV for %d symbols...", len(raw_symbols))
    query = f"""
        SELECT symbol, timestamp AS date, open, high, low, close, volume
        FROM `{BARS_TABLE}`
        WHERE symbol IN UNNEST(@symbols)
        ORDER BY symbol, timestamp
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ArrayQueryParameter("symbols", "STRING", raw_symbols),
        ]
    )
    ohlcv = bq.query(query, job_config=job_config).to_dataframe()
    ohlcv["date"] = pd.to_datetime(ohlcv["date"]).dt.tz_localize(None)
    # Normalize symbol
    ohlcv["symbol"] = ohlcv["symbol"].str.replace(r"^HK\.", "", regex=True).str.lstrip("0")
    # Deduplicate: bars has both "0005" and "HK.00005" rows for same symbol+date
    n_before = len(ohlcv)
    ohlcv = ohlcv.drop_duplicates(subset=["symbol", "date"])
    logger.info("OHLCV: %d rows (deduped from %d), %d unique symbols",
                len(ohlcv), n_before, ohlcv["symbol"].nunique())

    # ── Step 3: Compute factors per symbol ──
    fb = TechFactorBuilder()
    all_frames = []
    for sym, group in ohlcv.groupby("symbol"):
        stock_df = group.rename(columns={})  # keep columns as-is
        stock_df = stock_df.sort_values("date").reset_index(drop=True)
        try:
            # compute_factors returns all 39 factors + 2 labels
            factors = fb.compute_factors(stock_df)
            if factors is not None and not factors.empty:
                factors["symbol"] = sym
                n = len(factors)
                factors["date"] = stock_df["date"].values[:n]
                all_frames.append(factors)
        except Exception as e:
            logger.debug("Factor computation failed for %s: %s", sym, e)

    if not all_frames:
        logger.error("No factor data computed")
        return pd.DataFrame()

    result = pd.concat(all_frames, ignore_index=True)
    logger.info("Factor dataset: %d rows × %d cols, %d stocks",
                len(result), len(result.columns), result["symbol"].nunique())

    # Drop rows where label is NaN
    n_before = len(result)
    result = result.dropna(subset=[LABEL])
    logger.info("After dropna(label): %d rows (dropped %d)",
                len(result), n_before - len(result))

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
    logger.info("Features: %d, Train: %d rows (%s → %s), Val: %d rows (%s → %s)",
                len(feature_cols),
                len(train_df), train_df["date"].min().date(), train_df["date"].max().date(),
                len(val_df), val_df["date"].min().date(), val_df["date"].max().date())

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
    logger.info("🏆 Best trial #%d: RMSE=%.6f  IC=%.4f",
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
            "mode": "explicit",
            "n_factors": len(feature_cols),
        },
        "data": {
            "label": LABEL,
            "market": "hk",
            "date_range": f"{train_df['date'].min().date()}_{val_df['date'].max().date()}",
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
