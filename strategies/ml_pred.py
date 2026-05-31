"""MLPredStrategy — LightGBM prediction-driven stock selection.

Uses FactorRegistry + TechFactorBuilder + ModelTrainer to train a model
on historical data, then predicts top-K stocks each rebalance period.
"""
from __future__ import annotations

import logging
import numpy as np
import pandas as pd

from engine.strategy import Strategy, Signal

logger = logging.getLogger(__name__)


class MLPredStrategy(Strategy):
    """ML prediction-driven strategy.

    Trains LightGBM on historical factor data during on_init(),
    then predicts expected returns for each bar and buys top-K.
    
    Parameters
    ----------
    market : str
        Target market ("us", "hk").
    train_start : str
        Training data start date YYYY-MM-DD.
    train_end : str
        Training data end date YYYY-MM-DD.
    top_k : int
        Number of stocks to hold.
    rebalance_every : int
        Bars between rebalances.
    model_type : str
        "lightgbm" or "ridge".
    factor_top_n : int
        Max factors to use from FactorRegistry.
    """

    market: str = "us"
    train_start: str = "2020-01-01"
    train_end: str = "2025-12-31"
    top_k: int = 10
    rebalance_every: int = 5
    model_type: str = "lightgbm"
    factor_top_n: int = 15

    def __init__(self, **kwargs):
        super().__init__()
        for k, v in kwargs.items():
            if hasattr(self, k):
                setattr(self, k, v)
        self._trained = False
        self._model_trainer = None
        self._model = None
        self._last_rebalance = -self.rebalance_every
        self._all_scores_history: dict = {}

    def on_init(self, ctx):
        self._last_rebalance = -self.rebalance_every
        self._model_trainer = None
        self._model = None          # trained model object (lgb.Booster or sklearn)
        self._trained = False
        self._all_scores_history: dict[int, dict[str, float]] = {}

        symbols = list(ctx.universe)
        if not symbols:
            logger.warning("MLPredStrategy: no symbols in universe")
            return

        try:
            self._train(symbols)
        except Exception:
            logger.exception("MLPredStrategy training failed")

    def _train(self, symbols):
        """Train LightGBM on historical factor data from BQ."""
        from ml.trainer import ModelTrainer
        
        trainer = ModelTrainer(factor_path=None)
        df = trainer.load_from_bq(
            symbols=symbols,
            start=self.train_start,
            end=self.train_end,
            market=self.market,
            top_n=self.factor_top_n,
        )

        if df.empty:
            logger.warning("No factor data loaded for training")
            return

        train_df, val_df, _ = trainer.split_data(
            train_end="2024-12-31",
            val_end=self.train_end,
        )

        if self.model_type == "lightgbm":
            result = trainer.train_lightgbm(train_df, val_df)
        elif self.model_type == "ridge":
            result = trainer.train_ridge(train_df, val_df)
        else:
            logger.warning("Unknown model_type: %s", self.model_type)
            return

        self._model_trainer = trainer
        self._model = result.get("model")
        self._trained = True
        logger.info("MLPredStrategy trained: %d features, RMSE=%.4f",
                     len(trainer.feature_cols),
                     result.get("rmse_val", result.get("rmse", 0.0)))
    
    def _is_trainer_broken(self):
        """Check if trainer and model are usable."""
        if self._model_trainer is None:
            return True
        if self._model is None:
            return True
        return False

    def on_bar(self, ctx, bar: int) -> list[Signal]:
        if not self._trained:
            return []
        if bar - self._last_rebalance < self.rebalance_every:
            return []
        if bar < 20:
            return []

        self._last_rebalance = bar
        symbols = list(ctx.universe)
        if not symbols:
            return []

        try:
            scores = self._predict_scores(ctx, bar, symbols)
            if not scores:
                return []

            ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)[:self.top_k]
            self._all_scores_history[bar] = scores

            signals = []
            for sym, _score in ranked:
                signals.append(Signal.buy(sym, weight=1.0 / self.top_k))
            return signals
        except Exception:
            logger.warning("ML prediction failed at bar %d", bar, exc_info=True)
            return []

    def _predict_scores(self, ctx, bar: int, symbols: list[str]) -> dict[str, float]:
        """Predict expected return for each symbol at current bar."""
        from factors.tech_builder import TechFactorBuilder
        from factors.registry import FactorRegistry

        registry = FactorRegistry()
        active = registry.get_active(self.market)
        if active.empty:
            factor_names = ["ret_1d", "ret_5d", "vol_5d", "vol_20d", "rsi_14"]
        else:
            top = active.head(self.factor_top_n)
            factor_names = [f.replace(f"{self.market}_", "", 1)
                          for f in top["factor_id"].tolist()]

        # Build OHLCV history window per symbol for factor computation
        window = 100
        start_idx = max(0, bar - window)

        # Collect OHLCV per symbol
        symbol_ohlcv: dict[str, list[dict]] = {}
        for i in range(start_idx, bar + 1):
            dt = ctx.data.timestamp[i]
            for sym in symbols:
                close_val = ctx.data.close.iloc[i].get(sym, np.nan)
                if pd.isna(close_val):
                    continue
                open_val = (ctx.data.open.iloc[i].get(sym, close_val)
                           if hasattr(ctx.data, 'open') and ctx.data.open is not None
                           else close_val)
                high_val = (ctx.data.high.iloc[i].get(sym, close_val)
                           if hasattr(ctx.data, 'high') and ctx.data.high is not None
                           else close_val)
                low_val = (ctx.data.low.iloc[i].get(sym, close_val)
                          if hasattr(ctx.data, 'low') and ctx.data.low is not None
                          else close_val)
                vol_val = (ctx.data.volume.iloc[i].get(sym, 0)
                          if hasattr(ctx.data, 'volume') and ctx.data.volume is not None
                          else 0)
                symbol_ohlcv.setdefault(sym, []).append({
                    "date": dt,
                    "open": open_val,
                    "high": high_val,
                    "low": low_val,
                    "close": close_val,
                    "volume": vol_val,
                })

        if self._is_trainer_broken():
            return {}

        fb = TechFactorBuilder()
        scores: dict[str, float] = {}
        for sym in symbols:
            rows = symbol_ohlcv.get(sym, [])
            if len(rows) < 20:
                scores[sym] = 0.0
                continue
            sym_df = pd.DataFrame(rows)
            try:
                factors = fb.compute(factor_names, sym_df)
            except Exception:
                scores[sym] = 0.0
                continue
            if factors.empty:
                scores[sym] = 0.0
                continue
            latest = factors.iloc[-1:]
            try:
                preds = self._model_trainer.predict(self._model, latest)
            except Exception:
                scores[sym] = 0.0
                continue
            if preds is None or len(preds) == 0:
                scores[sym] = 0.0
                continue
            if isinstance(preds, pd.Series):
                scores[sym] = float(preds.values[0]) if len(preds) > 0 else 0.0
            elif isinstance(preds, np.ndarray):
                scores[sym] = float(preds[0]) if len(preds) > 0 else 0.0
            else:
                scores[sym] = 0.0

        return scores
