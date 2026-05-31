"""
ML 模型训练引擎 — 独立于 Engine

Models: Linear(OLS/Ridge) baseline → LightGBM
Ported from hk-quant/src/model_trainer.py.
"""
import pandas as pd
import numpy as np
from pathlib import Path
import logging
import pickle
import warnings
from typing import Optional, Tuple

from sklearn.linear_model import LinearRegression, Ridge
from sklearn.model_selection import TimeSeriesSplit
from sklearn.metrics import mean_squared_error
from sklearn.preprocessing import StandardScaler
from scipy.stats import spearmanr

import lightgbm as lgb

warnings.filterwarnings("ignore")
logger = logging.getLogger(__name__)


class ModelTrainer:
    """多因子模型训练器 — 独立于 Engine 运行。

    Provides:
        OLS baseline → Ridge → LightGBM (with early stopping)
        IC evaluation, predict, save/load.
    """

    def __init__(self, factor_path: Optional[str] = "./data/factors/factors.parquet"):
        """Initialize model trainer.

        Args:
            factor_path: Path to factor parquet file, or None if data
                         will be provided directly (e.g. for testing).
        """
        self.factor_path = factor_path
        self.factor_df: Optional[pd.DataFrame] = None
        self.feature_cols: list[str] = []
        self.label_col = "fwd_ret_5d"
        self.scaler = StandardScaler()

    # ── Data Loading ──────────────────────────────────────────────

    def load_data(self) -> pd.DataFrame:
        """Load factor data from factor_path.

        Auto-detects feature columns by excluding metadata and label columns.

        Returns:
            Loaded factor DataFrame.

        Raises:
            ValueError: If factor_path is None.
        """
        if self.factor_path is None:
            raise ValueError("factor_path is None; cannot load data")

        self.factor_df = pd.read_parquet(self.factor_path)
        logger.info(f"Loaded factors: {self.factor_df.shape}")

        # Feature columns (exclude metadata and labels)
        exclude = {"symbol", "date", "fwd_ret_5d", "fwd_ret_20d"}
        self.feature_cols = [
            c for c in self.factor_df.columns if c not in exclude
        ]
        logger.info(f"Features: {len(self.feature_cols)} columns")

        return self.factor_df

    def load_from_bq(
        self,
        symbols: list[str],
        start: str,
        end: str,
        market: str = "us",
        factor_ids: list[str] | None = None,
        top_n: int = 15,
    ):
        """Load factor data from BigQuery via FactorRegistry + TechFactorBuilder.

        Args:
            symbols: Stock symbols without prefix (e.g. ["AAPL", "MSFT"]).
            start: Start date YYYY-MM-DD.
            end: End date YYYY-MM-DD.
            market: "us" or "hk".
            factor_ids: Specific factor IDs from registry, or None to auto-select.
            top_n: Max active factors to use if factor_ids not specified.

        Returns:
            Factor DataFrame with symbol, date, and feature columns.
        """
        from google.cloud import bigquery
        from factors.tech_builder import TechFactorBuilder
        from factors.registry import FactorRegistry

        import pandas as pd

        # Step 1: Select factors from registry
        registry = FactorRegistry()
        active = registry.get_active(market)

        if active.empty:
            logger.warning(
                "No active factors in registry for market=%s. Falling back to all.",
                market,
            )
            factor_names = ["ret_1d", "ret_5d", "vol_5d", "vol_20d", "rsi_14"]  # fallback
        elif factor_ids:
            mask = active["factor_id"].isin(factor_ids)
            selected = active[mask]
            factor_names = [
                f.replace(f"{market}_", "", 1) for f in selected["factor_id"].tolist()
            ]
        else:
            top = active.head(top_n)
            factor_names = [
                f.replace(f"{market}_", "", 1) for f in top["factor_id"].tolist()
            ]

        logger.info("Using %d factors: %s", len(factor_names), factor_names[:5])

        # Step 2: Load OHLCV from BQ
        client = bigquery.Client()
        prefix = "US." if market == "us" else "HK."
        bq_symbols = [f"{prefix}{s}" for s in symbols]
        table = f"{registry.project}.{registry.dataset}.{market}_bars_1d"

        query = f"""
            SELECT symbol, timestamp, open, high, low, close, volume
            FROM `{table}`
            WHERE symbol IN UNNEST(@symbols)
              AND timestamp BETWEEN @start AND @end
            ORDER BY timestamp, symbol
        """
        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ArrayQueryParameter("symbols", "STRING", bq_symbols),
                bigquery.ScalarQueryParameter("start", "STRING", start),
                bigquery.ScalarQueryParameter("end", "STRING", end),
            ],
        )
        ohlcv = client.query(query, job_config=job_config).to_dataframe()
        ohlcv["timestamp"] = pd.to_datetime(ohlcv["timestamp"])
        ohlcv["symbol"] = ohlcv["symbol"].str.replace(prefix, "")

        if ohlcv.empty:
            logger.warning("No OHLCV data found for %s, %s-%s", symbols, start, end)
            return pd.DataFrame()

        logger.info(
            "Loaded %d OHLCV rows for %d symbols", len(ohlcv), len(symbols)
        )

        # Step 3: Compute factors per symbol
        fb = TechFactorBuilder()
        all_frames = []
        for sym, group in ohlcv.groupby("symbol"):
            stock_df = group.rename(columns={"timestamp": "date"})
            stock_df = stock_df.sort_values("date").reset_index(drop=True)
            try:
                factors = fb.compute(factor_names, stock_df)
                if factors is not None and not factors.empty:
                    factors["symbol"] = sym
                    n = len(factors)
                    factors["date"] = stock_df["date"].values[:n]
                    all_frames.append(factors)
            except Exception as e:
                logger.warning(
                    "Factor computation failed for %s: %s", sym, e
                )

        if not all_frames:
            logger.warning("No factor data computed for any symbol")
            return pd.DataFrame()

        self.factor_df = pd.concat(all_frames, ignore_index=True)

        # Step 4: Set feature columns
        exclude = {"symbol", "date", "fwd_ret_5d", "fwd_ret_20d"}
        self.feature_cols = [
            c for c in self.factor_df.columns if c not in exclude
        ]
        logger.info(
            "Loaded %d factor rows, %d features",
            len(self.factor_df),
            len(self.feature_cols),
        )
        return self.factor_df

    def load_data_from_bq(
        self,
        symbols: list[str],
        start: str,
        end: str,
        market: str = "us",
        factor_ids: list[str] | None = None,
        top_n: int = 15,
        factor_source: str = "tech",
    ):
        """Load factor data from BigQuery with factor_source control.

        Delegates to load_from_bq for tech factors, and extends to support
        fundamental / all factor sources.

        Parameters
        ----------
        symbols : list[str]
            Stock symbols without prefix (e.g. ["AAPL", "MSFT"]).
        start : str
            Start date YYYY-MM-DD.
        end : str
            End date YYYY-MM-DD.
        market : str
            "us" or "hk".
        factor_ids : list[str] or None
            Specific factor IDs, or None to auto-select.
        top_n : int
            Max active factors to use if factor_ids not specified.
        factor_source : str
            "tech" (default), "fundamental", or "all".
            - "tech": TechFactorBuilder only (existing behaviour).
            - "fundamental": FundamentalFactorBuilder only.
            - "all": Both tech and fundamental factors.

        Returns
        -------
        pd.DataFrame
            Factor DataFrame with symbol, date, and feature columns.
        """
        if factor_source == "tech":
            return self.load_from_bq(
                symbols=symbols, start=start, end=end,
                market=market, factor_ids=factor_ids, top_n=top_n,
            )

        if factor_source not in ("fundamental", "all"):
            raise ValueError(
                f"Unknown factor_source={factor_source!r}. "
                f"Use 'tech', 'fundamental', or 'all'."
            )

        import json as _json
        import pandas as pd
        from google.cloud import bigquery
        from factors.fundamental_builder import FundamentalFactorBuilder

        ffb = FundamentalFactorBuilder()
        client = bigquery.Client()

        # ── Helpers ──────────────────────────────────────────────────

        def _expand_json(df: pd.DataFrame) -> pd.DataFrame:
            """Expand JSON 'data' column into individual factor columns."""
            if "data" not in df.columns:
                return df
            parsed = df["data"].apply(
                lambda v: _json.loads(v) if isinstance(v, str) else (
                    v if isinstance(v, dict) else {}
                )
            )
            expanded = pd.DataFrame(parsed.tolist(), index=df.index)
            meta_cols = [c for c in df.columns if c != "data"]
            return pd.concat([df[meta_cols], expanded], axis=1)

        def _strip_prefix(df: pd.DataFrame) -> pd.DataFrame:
            """Strip 'US.' prefix from symbol column."""
            if "symbol" in df.columns:
                df["symbol"] = df["symbol"].astype(str).str.replace(
                    "US.", "", regex=False
                )
            return df

        # ── Load F10 tables at once (bulk queries, not per-symbol) ──
        raw_data: dict[str, pd.DataFrame] = {}

        # Financials
        fin_query = """
            SELECT * FROM `deductive-notch-495015-c2.quant.us_financials`
            WHERE symbol IN UNNEST(@syms)
        """
        fin_job = bigquery.QueryJobConfig(query_parameters=[
            bigquery.ArrayQueryParameter("syms", "STRING", symbols),
        ])
        financials = client.query(fin_query, job_config=fin_job).to_dataframe()
        if not financials.empty:
            financials = _expand_json(financials)
            # us_financials has date_time_str (format: YYYY/MM/DD), not date
            if "date_time_str" in financials.columns:
                financials["date"] = pd.to_datetime(
                    financials["date_time_str"], format="%Y/%m/%d", errors="coerce"
                )
            financials = _strip_prefix(financials)
            raw_data["financials"] = financials

        # Auxiliary tables (only for factor_source == "all")
        if factor_source == "all":
            for table_name, key in [
                ("us_valuation", "valuation"),
                ("us_capital_flow", "capital_flow"),
                ("us_analyst", "analyst"),
                ("us_shareholder", "short_interest"),
            ]:
                try:
                    aux_query = f"""
                        SELECT * FROM `deductive-notch-495015-c2.quant.{table_name}`
                        WHERE symbol IN UNNEST(@syms)
                    """
                    aux_job = bigquery.QueryJobConfig(query_parameters=[
                        bigquery.ArrayQueryParameter("syms", "STRING", symbols),
                    ])
                    aux_df = client.query(
                        aux_query, job_config=aux_job
                    ).to_dataframe()
                    if aux_df.empty:
                        continue

                    # Preprocess based on table type
                    if table_name in ("us_analyst", "us_capital_flow", "us_shareholder"):
                        # JSON-source tables: expand data column
                        aux_df = _expand_json(aux_df)
                    elif table_name == "us_valuation":
                        # Pivot valuation from long (valuation_type, value)
                        # to wide (pe_percentile, pb_percentile, ...)
                        if "valuation_type" in aux_df.columns and "value" in aux_df.columns:
                            aux_df["date"] = pd.to_datetime(
                                aux_df["date"], errors="coerce"
                            ).dt.date
                            aux_df = aux_df.pivot_table(
                                index=["symbol", "date"],
                                columns="valuation_type",
                                values="value",
                                aggfunc="first",
                            ).reset_index()

                    aux_df = _strip_prefix(aux_df)
                    raw_data[key] = aux_df
                except Exception as e:
                    logger.warning("Load %s failed: %s", table_name, e)

        # ── Compute factors per symbol ──────────────────────────────
        all_frames: list[pd.DataFrame] = []
        for sym in symbols:
            try:
                # Build per-symbol data_map from preprocessed bulk data
                sym_data: dict[str, pd.DataFrame] = {}
                for key, df in raw_data.items():
                    sym_df = df[df["symbol"] == sym].copy()
                    if sym_df.empty:
                        continue
                    sym_data[key] = sym_df

                if "financials" not in sym_data or sym_data["financials"].empty:
                    continue

                factors = ffb.compute(ffb.ALL_FACTOR_COLS, sym_data)
                if factors.empty:
                    continue

                factors["symbol"] = sym

                # Date alignment: use financials date column
                fin_sym = sym_data["financials"]
                if "date" in fin_sym.columns:
                    n = len(factors)
                    factors["date"] = fin_sym["date"].values[:n]
                else:
                    logger.warning("No date column in financials for %s", sym)
                    continue

                all_frames.append(factors)
            except Exception as e:
                logger.warning(
                    "Fundamental factor load failed for %s: %s", sym, e
                )

        if not all_frames:
            logger.warning("No fundamental factor data loaded for any symbol")
            return pd.DataFrame()

        fund_df = pd.concat(all_frames, ignore_index=True)

        # If "all", also load tech factors and merge
        if factor_source == "all":
            try:
                tech_df = self.load_from_bq(
                    symbols=symbols, start=start, end=end,
                    market=market, factor_ids=factor_ids, top_n=top_n,
                )
                if not tech_df.empty:
                    # Merge on symbol + date
                    fund_df = pd.merge(
                        fund_df, tech_df, on=["symbol", "date"],
                        how="outer", suffixes=("", "_tech"),
                    )
            except Exception as e:
                logger.warning("Tech factor merge failed: %s", e)

        self.factor_df = fund_df
        exclude = {"symbol", "date", "fwd_ret_5d", "fwd_ret_20d"}
        self.feature_cols = [
            c for c in self.factor_df.columns if c not in exclude
        ]
        logger.info(
            "load_data_from_bq: %d rows, %d features (source=%s)",
            len(self.factor_df), len(self.feature_cols), factor_source,
        )
        return self.factor_df

    # ── Data Splitting ─────────────────────────────────────────────

    def split_data(
        self,
        train_end: str = "2022-12-31",
        val_end: str = "2023-12-31",
        test_end: str = "2024-12-31",
    ) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
        """Time-series data split (no look-ahead bias).

        Args:
            train_end: Training set end date (inclusive).
            val_end: Validation set end date (inclusive).
            test_end: Test set end date (inclusive).

        Returns:
            (train, val, test) DataFrames.
        """
        if self.factor_df is None:
            self.load_data()

        df = self.factor_df.copy()
        df["date"] = pd.to_datetime(df["date"])

        train = df[df["date"] <= train_end]
        val = df[(df["date"] > train_end) & (df["date"] <= val_end)]
        test = df[(df["date"] > val_end) & (df["date"] <= test_end)]

        logger.info(
            f"Data split: train={len(train)} "
            f"({train['date'].min().date()}~{train['date'].max().date()}), "
            f"val={len(val)} "
            f"({val['date'].min().date()}~{val['date'].max().date()}), "
            f"test={len(test)} "
            f"({test['date'].min().date()}~{test['date'].max().date()})"
        )
        return train, val, test

    # ── Internal helpers ───────────────────────────────────────────

    def _prepare_xy(
        self, df: pd.DataFrame, fit_scaler: bool = False
    ) -> Tuple[np.ndarray, np.ndarray]:
        """Prepare feature matrix X and label vector y with scaling.

        Args:
            df: Input DataFrame.
            fit_scaler: If True, fit the scaler on this data (use for training).

        Returns:
            (X, y) arrays.
        """
        df_clean = df.dropna(subset=self.feature_cols + [self.label_col])
        X = df_clean[self.feature_cols].values.astype(np.float64)
        y = df_clean[self.label_col].values.astype(np.float64)

        if fit_scaler:
            X = self.scaler.fit_transform(X)
        else:
            X = self.scaler.transform(X)

        return X, y

    def _prepare_raw_xy(
        self, df: pd.DataFrame
    ) -> Tuple[pd.DataFrame, np.ndarray, np.ndarray]:
        """Prepare raw X/y for tree models (no scaling, NaN-fill with col means).

        Returns:
            (df_clean, X, y) — X is NaN-filled raw features.
        """
        df_clean = df.dropna(subset=self.feature_cols + [self.label_col])
        X = df_clean[self.feature_cols].values.astype(np.float64)
        y = df_clean[self.label_col].values.astype(np.float64)

        # NaN fill with column means
        col_means = np.nanmean(X, axis=0)
        inds = np.where(np.isnan(X))
        X[inds] = np.take(col_means, inds[1])

        return df_clean, X, y

    # ── OLS Baseline ───────────────────────────────────────────────

    def train_ols(self, train: pd.DataFrame, val: pd.DataFrame) -> dict:
        """OLS baseline: fit → predict → RMSE.

        Auto-fits scaler on training data.

        Args:
            train: Training DataFrame.
            val: Validation DataFrame.

        Returns:
            dict with keys: model, rmse.
        """
        logger.info("Training OLS baseline...")
        X_train, y_train = self._prepare_xy(train, fit_scaler=True)
        X_val, y_val = self._prepare_xy(val)

        model = LinearRegression()
        model.fit(X_train, y_train)

        pred_val = model.predict(X_val)
        rmse = float(np.sqrt(mean_squared_error(y_val, pred_val)))

        result = {
            "model": model,
            "name": "OLS",
            "rmse": rmse,
        }

        logger.info(f"  OLS: RMSE val={rmse:.6f}")
        return result

    # ── Ridge ──────────────────────────────────────────────────────

    def train_ridge(
        self, train: pd.DataFrame, val: pd.DataFrame, alpha: float = 1.0
    ) -> dict:
        """Ridge regression with L2 regularization.

        Auto-fits scaler on training data.

        Args:
            train: Training DataFrame.
            val: Validation DataFrame.
            alpha: L2 regularization strength.

        Returns:
            dict with keys: model, alpha, rmse.
        """
        logger.info(f"Training Ridge (alpha={alpha})...")
        X_train, y_train = self._prepare_xy(train, fit_scaler=True)
        X_val, y_val = self._prepare_xy(val)

        model = Ridge(alpha=alpha)
        model.fit(X_train, y_train)

        pred_val = model.predict(X_val)
        rmse = float(np.sqrt(mean_squared_error(y_val, pred_val)))

        result = {
            "model": model,
            "name": f"Ridge(α={alpha})",
            "alpha": alpha,
            "rmse": rmse,
        }
        logger.info(f"  Ridge: RMSE val={rmse:.6f}")
        return result

    # ── LightGBM ───────────────────────────────────────────────────

    def train_lightgbm(
        self,
        train: pd.DataFrame,
        val: pd.DataFrame,
    ) -> dict:
        """LightGBM with purged TimeSeriesSplit CV, early stopping.

        Uses raw features (no scaling needed for tree models).

        Args:
            train: Training DataFrame.
            val: Validation DataFrame.

        Returns:
            dict with keys: model, rmse_val, feature_importance, best_iteration.
        """
        logger.info("Training LightGBM...")

        # Use raw features (no scaling)
        train_clean, X_train, y_train = self._prepare_raw_xy(train)
        val_clean, X_val, y_val = self._prepare_raw_xy(val)

        params = {
            "objective": "regression",
            "metric": "rmse",
            "boosting_type": "gbdt",
            "num_leaves": 16,
            "learning_rate": 0.1,
            "feature_fraction": 0.6,
            "bagging_fraction": 0.6,
            "bagging_freq": 5,
            "min_data_in_leaf": 200,
            "min_sum_hessian_in_leaf": 1.0,
            "lambda_l1": 1.0,
            "lambda_l2": 1.0,
            "verbose": -1,
            "num_threads": 4,
            "seed": 42,
            "deterministic": True,
        }

        dtrain = lgb.Dataset(
            X_train, label=y_train, params={"feature_pre_filter": False}
        )
        dval = lgb.Dataset(
            X_val, label=y_val, reference=dtrain,
            params={"feature_pre_filter": False}
        )

        model = lgb.train(
            params,
            dtrain,
            num_boost_round=2000,
            valid_sets=[dtrain, dval],
            valid_names=["train", "val"],
            callbacks=[
                lgb.early_stopping(stopping_rounds=100),
                lgb.log_evaluation(period=0),
            ],
        )

        # Predict on validation
        pred_val = model.predict(X_val)
        rmse_val = float(np.sqrt(mean_squared_error(y_val, pred_val)))

        # Feature importance
        importance = pd.DataFrame({
            "feature": self.feature_cols,
            "importance": model.feature_importance(importance_type="gain"),
        }).sort_values("importance", ascending=False)

        result = {
            "model": model,
            "name": "LightGBM",
            "params": params,
            "best_iteration": model.best_iteration,
            "rmse_val": rmse_val,
            "feature_importance": importance,
        }

        logger.info(
            f"  LightGBM: RMSE val={rmse_val:.6f}, "
            f"best_iter={model.best_iteration}, "
            f"top feature: {importance.iloc[0]['feature']} "
            f"({importance.iloc[0]['importance']:.1f})"
        )
        return result

    # ── IC Evaluation ──────────────────────────────────────────────

    def evaluate_ic(
        self, model, df: pd.DataFrame, name: str = ""
    ) -> dict:
        """Compute Rank IC (overall + daily + ICIR).

        Uses scaler if fitted, otherwise raw features with NaN fill.

        Args:
            model: Trained model (LinearRegression, Ridge, or LightGBM Booster).
            df: DataFrame for evaluation.
            name: Optional model name for logging.

        Returns:
            dict with keys: overall_rank_ic, mean_daily_ic, daily_ic_std, icir.
        """
        df_clean = df.dropna(subset=self.feature_cols + [self.label_col])

        # Prepare features: use scaler for linear models, raw for tree models
        X_raw = df_clean[self.feature_cols].values.astype(np.float64)
        use_scaler = hasattr(self.scaler, "mean_") and not isinstance(model, lgb.Booster)
        if use_scaler:
            X = self.scaler.transform(X_raw)
        else:
            col_means = np.nanmean(X_raw, axis=0)
            inds = np.where(np.isnan(X_raw))
            X_raw[inds] = np.take(col_means, inds[1])
            X = X_raw

        pred = model.predict(X)
        actual = df_clean[self.label_col].values

        # Overall Rank IC
        ic, pval = spearmanr(pred, actual)

        # Daily Rank IC
        eval_df = df_clean.copy()
        eval_df["pred"] = pred

        daily_ic = []
        for date, group in eval_df.groupby("date"):
            if len(group) < 10:
                continue
            ic_d = group["pred"].rank().corr(group[self.label_col].rank())
            daily_ic.append({"date": date, "rank_ic": ic_d})

        ic_series = pd.DataFrame(daily_ic)
        ic_mean = ic_series["rank_ic"].mean() if len(ic_series) > 0 else float(ic)
        ic_std = ic_series["rank_ic"].std() if len(ic_series) > 0 else 0.0
        icir = ic_mean / ic_std if ic_std > 1e-8 else 0.0

        result = {
            "overall_rank_ic": float(ic),
            "mean_daily_ic": float(ic_mean),
            "daily_ic_std": float(ic_std),
            "icir": float(icir),
            "n_dates": len(ic_series),
        }

        display_name = name or "Model"
        logger.info(
            f"  {display_name}: overall IC={ic:.4f}, "
            f"daily IC={ic_mean:.4f}±{ic_std:.4f}, "
            f"ICIR={icir:.3f}, n_dates={len(ic_series)}"
        )
        return result

    # ── Prediction ─────────────────────────────────────────────────

    def predict(self, model, df: pd.DataFrame) -> pd.Series:
        """Generate predictions for a DataFrame.

        Uses scaler if fitted, otherwise raw features with NaN fill.

        Args:
            model: Trained model.
            df: DataFrame with feature columns.

        Returns:
            Flat pd.Series of predictions, indexed by the clean DataFrame's index.
        """
        df_clean = df.dropna(subset=self.feature_cols)
        X_raw = df_clean[self.feature_cols].values.astype(np.float64)

        use_scaler = hasattr(self.scaler, "mean_") and not isinstance(model, lgb.Booster)
        if use_scaler:
            X = self.scaler.transform(X_raw)
        else:
            col_means = np.nanmean(X_raw, axis=0)
            inds = np.where(np.isnan(X_raw))
            X_raw[inds] = np.take(col_means, inds[1])
            X = X_raw

        preds = model.predict(X)
        return pd.Series(preds.flatten(), index=df_clean.index, name="prediction")

    # ── Save / Load ────────────────────────────────────────────────

    def save_model(self, model, path: str):
        """Save model to path.

        LightGBM models are saved in native .txt format;
        scikit-learn models are saved as .pkl.

        Args:
            model: Model object to save.
            path: File path (without extension for LightGBM).
        """
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)

        # LightGBM Booster: save in native format
        if isinstance(model, lgb.Booster):
            txt_path = path.with_suffix(".txt")
            model.save_model(str(txt_path))
            logger.info(f"Saved LightGBM model to {txt_path}")
        else:
            # scikit-learn or other: pickle
            pkl_path = path.with_suffix(".pkl")
            with open(pkl_path, "wb") as f:
                pickle.dump(model, f)
            logger.info(f"Saved model to {pkl_path}")

    def load_model(self, path: str) -> object:
        """Load model from path.

        Detects LightGBM (.txt) vs pickle (.pkl) based on file existence.

        Args:
            path: File path (with or without extension).

        Returns:
            Loaded model object.

        Raises:
            FileNotFoundError: If neither .txt nor .pkl file exists.
        """
        path = Path(path)

        # Try LightGBM native format first
        txt_path = path.with_suffix(".txt")
        if txt_path.exists():
            model = lgb.Booster(model_file=str(txt_path))
            logger.info(f"Loaded LightGBM model from {txt_path}")
            return model

        # Try pickle
        pkl_path = path.with_suffix(".pkl")
        if pkl_path.exists():
            with open(pkl_path, "rb") as f:
                model = pickle.load(f)
            logger.info(f"Loaded model from {pkl_path}")
            return model

        raise FileNotFoundError(
            f"Model file not found: {txt_path} or {pkl_path}"
        )


# ── CLI ────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="ML Model Trainer CLI")
    parser.add_argument("--market", default="us", choices=["us", "hk"],
                        help="Market (default: us)")
    parser.add_argument("--symbols", nargs="+", default=["AAPL", "MSFT", "GOOGL"],
                        help="Stock symbols")
    parser.add_argument("--start", default="2020-01-01",
                        help="Start date YYYY-MM-DD")
    parser.add_argument("--end", default="2024-12-31",
                        help="End date YYYY-MM-DD")
    parser.add_argument("--factor-source", default="tech",
                        choices=["tech", "fundamental", "all"],
                        help="Factor source: tech, fundamental, or all (default: tech)")
    parser.add_argument("--model", default="lightgbm",
                        choices=["ols", "ridge", "lightgbm"],
                        help="Model type (default: lightgbm)")
    parser.add_argument("--output", default="./models",
                        help="Model output directory")
    parser.add_argument("--top-n", type=int, default=15,
                        help="Max factors from registry")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    trainer = ModelTrainer(factor_path=None)
    trainer.load_data_from_bq(
        symbols=args.symbols,
        start=args.start,
        end=args.end,
        market=args.market,
        top_n=args.top_n,
        factor_source=args.factor_source,
    )

    train, val, test = trainer.split_data()

    if args.model == "ols":
        result = trainer.train_ols(train, val)
    elif args.model == "ridge":
        result = trainer.train_ridge(train, val)
    else:
        result = trainer.train_lightgbm(train, val)

    ic_result = trainer.evaluate_ic(result["model"], test, name=args.model)
    trainer.save_model(result["model"], f"{args.output}/{args.model}_{args.factor_source}")
    print(f"\nTraining complete. ICIR: {ic_result['icir']:.3f}")
