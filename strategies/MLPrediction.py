"""MLPrediction — ML-driven stock selection using pre-trained models.

Loads a trained model from ModelRegistry, computes factor values on each
bar, and buys the top-K predicted stocks.
"""
from __future__ import annotations

import logging
import numpy as np
import pandas as pd

from engine.strategy import Strategy, Signal

logger = logging.getLogger(__name__)


class MLPrediction(Strategy):
    """ML prediction-driven strategy.

    Loads a pre-trained model from ModelRegistry, computes factor values
    on each bar, and buys top-K stocks by predicted return.
    
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
    warmup_bars: int = 20  # skip trading for first N bars (paper warmup); set 0 for live
    _SENTINEL: float = -999.0  # default score for failed/invalid predictions

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
            logger.warning("MLPrediction: no symbols in universe")
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
        if bar < self.warmup_bars:
            return []

        self._last_rebalance = bar
        symbols = self._symbols if hasattr(self, '_symbols') and self._symbols else list(ctx.universe)
        if not symbols:
            return []

        try:
            scores = self._predict_scores(ctx, bar, symbols)
            if not scores:
                return []

            # Filter out sentinel scores (failed/invalid predictions)
            valid = {s: v for s, v in scores.items() if v > self._SENTINEL / 2}
            if not valid:
                logger.debug("MLPrediction bar %d: all %d scores are sentinel, skipping",
                             bar, len(scores))
                return []  # No valid predictions → don't trade

            ranked = sorted(valid.items(), key=lambda x: (x[1], x[0]), reverse=True)[:self.top_k]
            top_symbols = {sym for sym, _ in ranked}
            self._all_scores_history[bar] = valid

            signals: list[Signal] = []

            # ── Sell: positions no longer in top-K ──
            for sym in list(ctx.portfolio.positions.keys()):
                if sym not in top_symbols:
                    signals.append(Signal.close(sym))

            # ── Buy: top-K symbols not yet held ──
            buy_count = sum(1 for s in top_symbols if s not in ctx.portfolio.positions)
            if buy_count > 0:
                buy_weight = 1.0 / max(buy_count, 1)
                for i, (sym, score) in enumerate(ranked):
                    if sym not in ctx.portfolio.positions:
                        signals.append(Signal.buy(
                            sym, weight=buy_weight, score=float(score), rank=i + 1,
                        ))

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
                continue
            sym_df = pd.DataFrame(rows)
            try:
                factors = fb.compute(factor_names, sym_df)
            except Exception:
                continue
            if factors.empty:
                continue
            latest = factors.iloc[-1:]
            try:
                preds = self._model_trainer.predict(self._model, latest)
            except Exception:
                continue
            if preds is None or len(preds) == 0:
                continue
            if isinstance(preds, pd.Series):
                scores[sym] = float(preds.values[0]) if len(preds) > 0 else self._SENTINEL
                if np.isnan(scores[sym]):
                    continue
            elif isinstance(preds, np.ndarray):
                scores[sym] = float(preds[0]) if len(preds) > 0 else self._SENTINEL
                if np.isnan(scores[sym]):
                    continue
            else:
                scores[sym] = self._SENTINEL

        return scores
