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
    model_name: str = "momentum_lgbm"
    model_version: int | str = "latest"

    def __init__(self, **kwargs):
        super().__init__()
        for k, v in kwargs.items():
            if hasattr(self, k):
                setattr(self, k, v)
        self._trained = False
        self._model_trainer = None
        self._model = None
        self._config = None
        self._features = None
        self._last_rebalance = -self.rebalance_every
        self._all_scores_history: dict = {}

    def on_init(self, ctx, symbols=None):
        self._last_rebalance = -self.rebalance_every
        self._model_trainer = None
        self._model = None          # trained model object (lgb.Booster or sklearn)
        self._trained = False
        self._all_scores_history: dict[int, dict[str, float]] = {}

        # Use caller-supplied symbols if provided, else fall back to ctx.universe
        self._symbols = list(symbols) if symbols else list(ctx.universe)
        if not self._symbols:
            logger.warning("MLPredStrategy: no symbols in universe")
            return

        self._load_model()

    def _get_symbols(self) -> list[str]:
        """Return list of symbols from the strategy context."""
        return self._symbols if hasattr(self, '_symbols') and self._symbols else []

    def _load_model(self):
        """Load trained model from ModelRegistry. Fails fast if not found."""
        from ml.registry import ModelRegistry
        bundle = ModelRegistry.load(self.model_name, self.model_version)
        self._model = bundle.model
        self._config = bundle.config
        self._features = bundle.features
        # Also store the trainer for predict
        from ml.trainer import ModelTrainer
        self._model_trainer = ModelTrainer(factor_path=None)
        # Set feature_cols so predict() finds the right columns
        self._model_trainer.feature_cols = self._features
        logger.info("Loaded %s v%d (%d features)",
                    bundle.name, bundle.version, len(bundle.features))
        self._trained = True

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
        # Use runner-supplied symbols (from on_init), fall back to ctx.universe
        symbols = self._symbols if hasattr(self, '_symbols') and self._symbols else list(ctx.universe)
        if not symbols:
            return []

        try:
            scores = self._predict_scores(ctx, bar, symbols)
            if not scores:
                return []

            ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)[:self.top_k]
            self._all_scores_history[bar] = scores

            signals = []
            for i, (sym, _score) in enumerate(ranked):
                signals.append(Signal.buy(sym, weight=1.0 / self.top_k, score=float(_score), rank=i+1))
            return signals
        except Exception:
            logger.warning("ML prediction failed at bar %d", bar, exc_info=True)
            return []

    def _predict_scores(self, ctx, bar: int, symbols: list[str]) -> dict[str, float]:
        """Predict expected return for each symbol at current bar."""
        from factors.tech_builder import TechFactorBuilder

        # Use the EXACT features the model was trained on
        factor_names = self._features if self._features else []
        if not factor_names:
            # Fallback: use registry top N
            from factors.registry import FactorRegistry
            registry = FactorRegistry()
            active = registry.get_active(self.market)
            if active.empty:
                factor_names = ["ret_1d", "ret_5d", "vol_5d", "vol_20d", "rsi_14"]
            else:
                top = active.head(self.factor_top_n)
                factor_names = top["factor_id"].tolist()

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
