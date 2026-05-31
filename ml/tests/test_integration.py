"""End-to-end integration test: TechFactorBuilder → ModelTrainer → Engine with ML predictions."""

import numpy as np
import pandas as pd
import pytest

from factors.tech_builder import TechFactorBuilder
from ml.trainer import ModelTrainer
from engine.data import DataFrameSource
from engine.engine import Engine
from engine.config import BacktestConfig
from engine.strategy import Strategy, Signal


def make_ohlcv(sym, n_days=252):
    """Create synthetic OHLCV data with a known momentum signal.

    Prices follow a sine wave + random walk, so momentum/trend factors
    have predictive power for forward returns — enough for LightGBM
    to learn something meaningful even with few symbols.
    """
    # Use a deterministic seed based on symbol so each symbol is different
    rng = np.random.RandomState(hash(sym) % 2**32)
    dates = pd.date_range("2023-01-01", periods=n_days, freq="B")

    # Price = strong sine trend + smaller random walk + symbol-specific offset
    t = np.linspace(0, 3 * np.pi, n_days)
    trend = 30 * np.sin(t)
    rw = np.cumsum(rng.randn(n_days) * 1.0)
    offset = rng.uniform(-20, 20)
    price = 100 + offset + trend + rw

    df = pd.DataFrame({
        "date": dates,
        "symbol": sym,
        "open": price + rng.randn(n_days) * 0.3,
        "high": price + abs(rng.randn(n_days)) * 1.0,
        "low": price - abs(rng.randn(n_days)) * 1.0,
        "close": price,
        "volume": rng.randint(1000, 100000, n_days) * 1.0,
    })
    return df


class MLFactorStrategy(Strategy):
    """Simple ML-driven strategy: buy high-pred, close low-pred."""

    def on_bar(self, ctx, bar):
        # Get predictions via ctx.predictions (set by Engine before on_bar)
        preds = ctx.predictions
        if preds is None:
            return []
        signals = []
        for sym, score in preds.items():
            if score > 0.5:
                signals.append(Signal.buy(sym, weight=0.5))
            elif score < -0.5:
                signals.append(Signal.close(sym))
        return signals


@pytest.mark.slow
def test_full_ml_to_engine_pipeline():
    """
    End-to-end: TechFactorBuilder → ModelTrainer → Engine with ML predictions.

    Verifies:
      1. Factors are computed from synthetic data
      2. A LightGBM model can be trained on those factors
      3. Predictions feed into DataFrameSource as a 'pred' matrix
      4. Strategy reads predictions via ctx.predictions
      5. Engine runs, produces an equity curve with positive final equity
    """
    n_days = 300  # ~14 months — enough bars for train/val/test split
    symbols = [f"STOCK_{chr(65+i)}" for i in range(20)]  # 20 symbols for cross-sectional signal

    # ── 1. Build factor dataset for all symbols ──────────────────────
    fb = TechFactorBuilder()

    # Store original close prices separately (before factor processing)
    close_records = []
    all_dfs = []
    for sym in symbols:
        df = make_ohlcv(sym, n_days)
        factors = fb.compute_factors(df)
        factors["symbol"] = sym
        factors["date"] = df["date"].values
        all_dfs.append(factors)
        close_records.append(df[["date", "symbol", "close"]].copy())

    # Build and process factor dataset
    factor_df = pd.concat(all_dfs, ignore_index=True)
    factor_df = fb.process_factors(factor_df)
    assert len(factor_df) > 0, "Factor dataset should not be empty"

    # Merge original close prices back (unaffected by z-scoring)
    close_df = pd.concat(close_records, ignore_index=True)
    factor_df = factor_df.merge(close_df, on=["date", "symbol"], how="left")

    # Inject known signal: make fwd_ret_5d partially predictable from ret_5d
    np.random.seed(42)
    noise = np.random.randn(len(factor_df)) * 0.005
    factor_df["fwd_ret_5d"] = 0.8 * factor_df["ret_5d"] + noise

    # ── 2. Train LightGBM model ──────────────────────────────────────
    trainer = ModelTrainer(factor_path=None)
    trainer.feature_cols = [
        c for c in factor_df.columns
        if c not in ("symbol", "date", "fwd_ret_5d", "fwd_ret_20d")
    ]
    trainer.label_col = "fwd_ret_5d"

    train = factor_df[factor_df["date"] <= "2023-09-30"]
    val = factor_df[(factor_df["date"] > "2023-09-30") & (factor_df["date"] <= "2023-11-30")]

    assert len(train) > 0, "Training set should not be empty"
    assert len(val) > 0, "Validation set should not be empty"

    result = trainer.train_lightgbm(train, val)
    model = result["model"]
    assert model is not None, "LightGBM training failed"
    assert result["best_iteration"] is not None and result["best_iteration"] >= 1

    # ── 3. Generate predictions on the out-of-sample period ──────────
    test = factor_df[factor_df["date"] > "2023-11-30"]
    assert len(test) > 0, "Test set should not be empty"

    pred_series = trainer.predict(model, test)
    test_with_pred = test.copy()
    test_with_pred["pred"] = pred_series.values

    # Pivot to T×symbols matrices
    close_pivot = test_with_pred.pivot(index="date", columns="symbol", values="close")
    pred_pivot = test_with_pred.pivot(index="date", columns="symbol", values="pred")

    assert list(close_pivot.columns) == symbols
    assert list(pred_pivot.columns) == symbols

    # ── 4. Create DataSource with predictions ────────────────────────
    data_source = DataFrameSource(close=close_pivot, pred=pred_pivot)

    # ── 5. Run backtest ──────────────────────────────────────────────
    config = BacktestConfig(initial_capital=100_000)
    strategy = MLFactorStrategy()
    engine_result = Engine(config).run(strategy, data_source)

    # ── 6. Verify results ────────────────────────────────────────────
    equity = engine_result.portfolio.equity_curve
    assert len(equity) > 0, "Equity curve should have entries"
    assert equity.iloc[-1] > 0, "Final equity should be positive"
    assert equity.iloc[-1] != 100_000, "Model should generate trades (equity should differ from initial capital)"
    assert engine_result.strategy_name == "MLFactorStrategy"

    print(
        f"✅ Full ML → Engine pipeline verified: "
        f"{len(equity)} bars, "
        f"final equity={equity.iloc[-1]:.2f}, "
        f"LightGBM best_iter={result['best_iteration']}"
    )
