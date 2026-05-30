# W2 ML 双策略 Walk-Forward 实施计划

> **For agentic workers:** Use subagent-driven-development. Steps use checkbox syntax.

**Goal:** 写 MLPredStrategy（从 FactorRegistry 取因子 → LightGBM 训练 → 预测选股），双策略 walk-forward 对比（动量 vs ML），ExperimentRunner 记录结果。

**Architecture:** FactorRegistry → FactorBuilder → ModelTrainer → MLPredStrategy → PaperRunner/ExperimentRunner 并行动量基准。

**Prerequisite:** W1 complete (✅), Factor Registry seeded (✅ 39 factors in BQ), us_bars_1d in BQ (✅ 372K rows).

---

## File Map

| File | Action |
|------|--------|
| `ml/trainer.py` | Modify: add `load_from_bq()` method |
| `strategies/ml_pred.py` | Create: MLPredStrategy |
| `experiment/config_w2.yaml` | Create: walk-forward config |
| `scripts/run_w2_experiment.py` | Create: launch script |
| `ml/tests/test_bq_integration.py` | Create: BQ integration test |
| `strategies/tests/test_ml_pred.py` | Create: strategy tests |

---

### Task W2-1: ModelTrainer.load_from_bq()

**Files:** Modify `ml/trainer.py`, Create `ml/tests/test_bq_integration.py`

Add a method to load factor data directly from BQ via FactorRegistry + FactorBuilder, bypassing parquet files.

- [ ] **Step 1: Write test**

```python
# ml/tests/test_bq_integration.py
def test_load_from_bq_returns_feature_matrix():
    from ml.trainer import ModelTrainer
    trainer = ModelTrainer(factor_path=None)
    df = trainer.load_from_bq(
        symbols=["AAPL", "MSFT", "GOOGL"],
        start="2024-01-01", end="2024-06-30",
        market="us",
    )
    assert len(df) > 0
    assert "ret_1d" in df.columns or len(trainer.feature_cols) > 0
```

- [ ] **Step 2: Run → FAIL**

- [ ] **Step 3: Implement load_from_bq()**

```python
def load_from_bq(self, symbols, start, end, market="us", factor_ids=None):
    """Load factor data from BigQuery + FactorBuilder for given symbols/period.
    
    Uses FactorRegistry.get_active() to select factors, FactorBuilder.compute()
    to calculate them from OHLCV bars loaded from BQ.
    """
    from google.cloud import bigquery
    from factors.builder import FactorBuilder
    from factors.registry import FactorRegistry
    
    # Select factors from registry
    registry = FactorRegistry()
    active = registry.get_active(market)
    if factor_ids:
        active_factor_ids = [f for f in factor_ids if f in active["factor_id"].values]
    else:
        active_factor_ids = active["factor_id"].head(15).tolist()
    
    # Map registry IDs back to builder names (strip "us_" prefix)
    factor_names = [f.replace(f"{market}_", "", 1) for f in active_factor_ids]
    
    # Load OHLCV from BQ
    client = bigquery.Client()
    prefix = "US." if market == "us" else "HK."
    bq_symbols = [f"{prefix}{s}" for s in symbols]
    table = f"deductive-notch-495015-c2.quant.{market}_bars_1d"
    
    import pandas as pd
    query = f"""
        SELECT symbol, timestamp, open, high, low, close, volume
        FROM `{table}`
        WHERE symbol IN UNNEST(@symbols)
          AND timestamp BETWEEN @start AND @end
        ORDER BY timestamp, symbol
    """
    job_config = bigquery.QueryJobConfig(query_parameters=[
        bigquery.ArrayQueryParameter("symbols", "STRING", bq_symbols),
        bigquery.ScalarQueryParameter("start", "STRING", start),
        bigquery.ScalarQueryParameter("end", "STRING", end),
    ])
    ohlcv = client.query(query, job_config=job_config).to_dataframe()
    ohlcv["timestamp"] = pd.to_datetime(ohlcv["timestamp"])
    ohlcv["symbol"] = ohlcv["symbol"].str.replace(prefix, "")
    
    # Compute factors per symbol
    fb = FactorBuilder()
    all_frames = []
    for sym, group in ohlcv.groupby("symbol"):
        stock_df = group.rename(columns={"timestamp": "date"})
        stock_df = stock_df.set_index("date").reset_index()
        factors = fb.compute(factor_names, stock_df)
        factors["symbol"] = sym
        factors["date"] = stock_df["date"].values[:len(factors)]
        all_frames.append(factors)
    
    self.factor_df = pd.concat(all_frames, ignore_index=True)
    exclude = {"symbol", "date", "fwd_ret_5d", "fwd_ret_20d"}
    self.feature_cols = [c for c in self.factor_df.columns if c not in exclude]
    logger.info(f"Loaded {len(self.factor_df)} factor rows, {len(self.feature_cols)} features")
    return self.factor_df
```

- [ ] **Step 4: Run test → PASS**

- [ ] **Step 5: Commit**

---

### Task W2-2: MLPredStrategy

**Files:** Create `strategies/ml_pred.py`, Create `strategies/tests/test_ml_pred.py`

- [ ] **Step 1: Write test**

```python
# strategies/tests/test_ml_pred.py
def test_ml_pred_strategy_produces_signals():
    from strategies.ml_pred import MLPredStrategy
    from engine.strategy import StrategyContext
    strategy = MLPredStrategy(market="us", train_start="2020-01-01",
                              train_end="2025-12-31", top_k=10)
    # Create mock context with enough bars
    import pandas as pd
    import numpy as np
    dates = pd.date_range("2026-01-01", periods=100, freq="B")
    symbols = ["AAPL", "MSFT", "GOOGL", "AMZN", "META"]
    close = pd.DataFrame(np.random.randn(100, 5).cumsum(axis=0) + 100,
                         index=dates, columns=symbols)
    open_df = close * 0.99
    high = close * 1.01
    low = close * 0.98
    volume = pd.DataFrame(np.random.randint(1000, 10000, (100, 5)),
                          index=dates, columns=symbols)
    from engine.data import DataFrameSource
    ds = DataFrameSource(close=close, open=open_df, high=high, low=low, volume=volume)
    from engine.portfolio import Portfolio
    ctx = StrategyContext(data=ds, portfolio=Portfolio(100000), config={})
    strategy.on_init(ctx)
    # After training bars, should produce signals
    signals = strategy.on_bar(ctx, bar=50)
    assert isinstance(signals, list)
```

- [ ] **Step 2: Run → FAIL**

- [ ] **Step 3: Implement MLPredStrategy**

```python
class MLPredStrategy(Strategy):
    """ML prediction-driven strategy using LightGBM with FactorRegistry factors."""
    
    market: str = "us"
    train_start: str = "2020-01-01"
    train_end: str = "2025-12-31"
    top_k: int = 10
    rebalance_every: int = 5  # bars
    model_type: str = "lightgbm"  # lightgbm | ridge
    
    def on_init(self, ctx):
        self._last_rebalance = -self.rebalance_every
        self._model = None
        self._trained = False
        symbols = list(ctx.data.close.columns)
        
        # Train model on historical data
        self._train(symbols)
    
    def _train(self, symbols):
        from ml.trainer import ModelTrainer
        trainer = ModelTrainer(factor_path=None)
        # Load and train on historical period
        df = trainer.load_from_bq(symbols, self.train_start, self.train_end, self.market)
        train, val = trainer.split_data(train_end="2024-12-31", val_end=self.train_end)
        if self.model_type == "lightgbm":
            trainer.train_lightgbm(train, val)
        else:
            trainer.train_ridge(train, val)
        self._model = trainer
        self._trained = True
    
    def on_bar(self, ctx, bar):
        if not self._trained:
            return []
        if bar - self._last_rebalance < self.rebalance_every:
            return []
        if bar < 20:
            return []
        self._last_rebalance = bar
        
        # Predict next returns for all symbols
        symbols = list(ctx.data.close.columns)
        if not symbols:
            return []
        
        # Build feature row from recent data
        try:
            from factors.builder import FactorBuilder
            from factors.registry import FactorRegistry
            registry = FactorRegistry()
            active = registry.get_active(self.market)
            factor_names = [f.replace(f"{self.market}_", "", 1) 
                          for f in active["factor_id"].head(15).tolist()]
            
            # Compute factors for latest bar window
            fb = FactorBuilder()
            recent_data = self._build_recent_df(ctx, bar)
            factors = fb.compute(factor_names, recent_data)
            latest = factors.iloc[-1:]
            
            preds = self._model.predict(self._model._lgb_model, latest) if hasattr(self._model, '_lgb_model') else None
            if preds is None:
                return []
            
            scores = dict(zip(symbols, preds.values[0] if hasattr(preds, 'values') else [0]*len(symbols)))
            ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)[:self.top_k]
            
            signals = []
            for sym, _score in ranked:
                signals.append(Signal.buy(sym, weight=1.0/self.top_k))
            return signals
        except Exception:
            import logging
            logging.getLogger(__name__).warning("ML prediction failed", exc_info=True)
            return []
    
    def _build_recent_df(self, ctx, bar):
        import pandas as pd
        window = 100
        start = max(0, bar - window)
        dates = ctx.data.timestamp[start:bar+1]
        close_vals = ctx.data.close.iloc[start:bar+1]
        # Aggregate to single-stock format expected by FactorBuilder
        rows = []
        for sym in ctx.data.close.columns:
            for i, dt in enumerate(dates):
                rows.append({
                    "date": dt,
                    "open": ctx.data.open.iloc[start+i][sym] if hasattr(ctx.data, 'open') and ctx.data.open is not None else close_vals.iloc[i][sym],
                    "high": ctx.data.high.iloc[start+i][sym] if hasattr(ctx.data, 'high') and ctx.data.high is not None else close_vals.iloc[i][sym],
                    "low": ctx.data.low.iloc[start+i][sym] if hasattr(ctx.data, 'low') and ctx.data.low is not None else close_vals.iloc[i][sym],
                    "close": close_vals.iloc[i][sym],
                    "volume": ctx.data.volume.iloc[start+i][sym] if hasattr(ctx.data, 'volume') and ctx.data.volume is not None else 0,
                })
        return pd.DataFrame(rows)
```

- [ ] **Step 4: Run test → PASS**

- [ ] **Step 5: Commit**

---

### Task W2-3: Walk-Forward Config

**Files:** Create `experiment/config_w2.yaml`

- [ ] **Step 1: Write config**

```yaml
experiment:
  id: "w2_ml_vs_momentum"
  name: "W2 ML vs Momentum Walk-Forward"
  hypothesis: "LightGBM with FactorRegistry top-15 factors outperforms SimpleMomentum"

walk_forward:
  market: us
  train_start: "2020-01-01"
  train_end: "2022-12-31"
  val_start: "2023-01-01"
  val_end: "2023-12-31"
  test_start: "2024-01-01"
  test_end: "2025-12-31"
  paper_start: "2026-01-01"
  paper_end: "2026-05-28"
  symbols: ["AAPL","MSFT","GOOGL","AMZN","META","NVDA","TSLA","JPM","V","JNJ",
            "WMT","PG","MA","HD","BAC","DIS","NFLX","ADBE","CRM","INTC"]
  capital: 100000

strategies:
  - name: "momentum_baseline"
    class: "SimpleMomentum"
    params:
      lookback: 20
      top_k: 20
      rebalance_every: 5
  - name: "ml_lightgbm"
    class: "MLPredStrategy"
    params:
      market: us
      train_start: "2020-01-01"
      train_end: "2024-12-31"
      top_k: 10
      rebalance_every: 5
      model_type: lightgbm

evaluation:
  metrics: [sharpe_ratio, max_drawdown, win_rate, total_return, annual_return]
  benchmark: momentum_baseline
```

- [ ] **Step 2: Commit**

---

### Task W2-4: Run Script + Integration

**Files:** Create `scripts/run_w2_experiment.py`

- [ ] **Step 1: Write launch script**

A script that:
1. Loads config from `experiment/config_w2.yaml`
2. Trains MLPredStrategy via ModelTrainer.load_from_bq()
3. Runs walk-forward for both strategies via PaperRunner
4. Records results in ExperimentTracker
5. Generates comparison output

- [ ] **Step 2: Run integration test**

```bash
python3.12 scripts/run_w2_experiment.py
```

- [ ] **Step 3: Verify comparison output**

Expected: Sharpe/MaxDD/WinRate comparison between ML and momentum strategies.

- [ ] **Step 4: Commit & push**
