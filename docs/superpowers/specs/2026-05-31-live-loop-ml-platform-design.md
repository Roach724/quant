# Live Loop & ML Platform — Design Spec

> Date: 2026-05-31  
> Status: Design approved, pending implementation plan  
> Scope: Live trading loop (paper + real), ML model registry, hyperparameter tuning, dataset versioning

---

## 1. Goals

1. **Live Loop**: Unified runner for paper trading and live trading, multi-market (US/HK/Crypto), fully observable (logs, snapshots, trade records, real-time dashboard, post-run HTML report with charts).
2. **ML Platform**: Replace ad-hoc training with MLflow Model Registry + Optuna hyperparameter tuning + versioned training datasets. All model consumers load from Registry via a single unified interface.
3. **Unified model loading**: `MLPredStrategy`, PaperRunner, LiveRunner, and future backtest scripts all call `ModelRegistry.load(name, version)`.

---

## 2. Architecture Overview

```
                         ┌──────────────────────────┐
                         │       LiveRunner          │
                         │   (live/runner.py)        │
                         │                          │
                         │  config.yaml ──→ mode:   │
                         │  paper → PaperBroker      │
                         │  live  → FutuStockBroker  │
                         │                          │
                         │  Main loop:               │
                         │  data → strategy → OMS → │
                         │  risk → dashboard         │
                         └──────┬───────────────────┘
                                │
        ┌───────────────────────┼───────────────────────┐
        │                       │                       │
   ┌────▼─────┐         ┌──────▼──────┐         ┌──────▼──────┐
   │ ml/       │         │ live/       │         │ dashboard/  │
   │ registry  │         │ observer    │         │ api.py      │
   │ tuner     │         │ reporter    │         │ index.html  │
   │ datasets  │         │ config      │         └─────────────┘
   │ trainer   │         │ runner      │
   └───────────┘         └─────────────┘
```

Three independent packages communicating via well-defined interfaces.

| Package | Responsibility | Key exports |
|---------|---------------|-------------|
| `ml/` | ModelRegistry, OptunaTuner, DatasetManager, ModelTrainer (existing) | `ModelRegistry.load()`, `OptunaTuner.tune()`, `DatasetManager.create/load()` |
| `live/` | LiveRunner main loop, Observer, Reporter, config | `LiveRunner(config_path)`, `Observer`, `Reporter` |
| `dashboard/` | Existing FastAPI + WebSocket extension | `/api/portfolio`, `/ws/stream` |

---

## 3. Live Loop (`live/`)

### 3.1 Lifecycle

```
启动 → 加载配置 → 初始化组件 → 等待开盘 → 主循环 → 收盘汇总 → 生成报告
```

### 3.2 Configuration (`live/config.yaml`)

```yaml
live:
  mode: paper              # paper | live
  market: us
  output_dir: output/live/

broker:
  paper:
    initial_capital: 100000
    slippage_bps: 5
    commission_bps: 1
    min_commission: 1.0
  live:
    type: futu_stock        # futu_stock | alpaca
    host: 127.0.0.1
    port: 11111
    max_position_pct: 0.2

strategy:
  name: MLPredStrategy
  model_name: momentum_lgbm    # ModelRegistry.load() 参数
  model_version: 3             # 版本号或 "latest"
  top_k: 5
  rebalance_every: 5
  fallback_strategy: SimpleMomentum  # 模型加载失败时的降级策略

schedule:
  pre_market_warmup: 300       # 开盘前提前拉数据(秒)
  bar_interval: 300            # 5分钟一根bar
  market_close_offset: 600     # 收盘后多久停止(秒)

risk:
  max_drawdown: 0.15
  max_daily_loss: 0.05
  position_size_pct: 0.2

observer:
  log_dir: output/live/
  snapshot_interval: 60        # 持仓快照间隔(秒)
  trade_log: true
  equity_curve: true

dashboard:
  port: 8090
  websocket: true
```

### 3.3 Main Loop

```python
class LiveRunner:
    def run(self):
        self._init_broker()          # PaperBroker or FutuStockBroker
        self._init_strategy()        # Strategy + ModelRegistry.load()
        self._init_risk()            # RiskGateway + RiskMonitor
        self._init_observer()        # Observer
        self._init_dashboard()       # FastAPI + WebSocket
        self._wait_for_market_open()
        
        while self.scheduler.market_is_open():
            bar = self._fetch_current_bar()
            
            # Scheduled snapshots
            if self.observer.snapshot_due(bar.time):
                self.observer.snapshot_portfolio()
            
            signals = self.strategy.on_bar(bar)
            
            for sig in signals:
                if not self.risk_gateway.check(sig):
                    continue
                
                order = self.broker.submit(sig)
                self.observer.record_signal(sig)
                if order.filled_qty > 0:
                    self.observer.record_trade(order)
            
            self.dashboard.push_state()
            self.observer.record_bar(bar)
        
        self.reporter.generate()
```

### 3.4 Paper vs Live Switching

```python
if config["live"]["mode"] == "paper":
    broker = PaperBroker(**config["broker"]["paper"])
else:
    broker = SUPPORTED_BROKERS[config["broker"]["live"]["type"]](
        **config["broker"]["live"]
    )
```

Both implement the `Broker` protocol from `oms/broker/__init__.py`. Transparent to the loop.

### 3.5 Observer — Output Files

```
output/live/
  us_paper_20260531_093000/       # {market}_{mode}_{timestamp}
    config.yaml                   # Runtime config copy
    trades.csv                    # time,symbol,side,qty,price,commission
    signals.csv                   # time,symbol,side,score,rank
    positions_snapshot.csv        # timestamp,symbol,qty,price,cost_basis,mkt_value,pnl_pct
    equity_curve.csv              # timestamp,equity,cash,portfolio_value
    daily_pnl.csv                 # date,equity,returns
    alerts.log                    # timestamp,level,message
    report.html                   # Post-run summary with charts
```

### 3.6 Reporter — HTML Report

Generated at end-of-run:
- Equity curve (matplotlib line chart)
- Drawdown curve
- Position concentration pie chart
- Signal frequency bar chart
- Summary table: return, sharpe, maxdd, win_rate, turnover
- Trade detail table (paginated)

### 3.7 Dashboard WebSocket Extension

New endpoint: `GET /ws/stream`
Pushes JSON on every bar:
```json
{
  "type": "state",
  "timestamp": "2026-06-01T14:35:00Z",
  "portfolio": {"equity": 105000, "cash": 20000, "positions_value": 85000},
  "positions": [{"symbol":"AAPL","qty":30,"price":300,"cost":280,"pnl_pct":7.1}],
  "last_signal": {"symbol":"NVDA","side":"buy","score":0.023},
  "alerts": []
}
```

---

## 4. ML Platform (`ml/`)

### 4.1 Directory Structure

```
ml/
  __init__.py
  trainer.py            # Existing — core training logic (OLS/Ridge/LightGBM)
  registry.py           # NEW — ModelRegistry (MLflow wrapper)
  tuner.py              # NEW — OptunaTuner
  datasets.py           # NEW — DatasetManager

models/                 # Model configs (in repo)
  momentum_lgbm/
    v3/
      config.yaml

models_artifacts/       # MLflow artifact cache (gitignored)
```

### 4.2 Model Configuration (`models/{name}/v{version}/config.yaml`)

Each model version has its own config, isolating factors, hyperparameters, and data:

```yaml
model:
  name: "momentum_lgbm"
  type: "lightgbm"                       # lightgbm | ridge | ols
  description: "US top 20 momentum + F10"

factors:
  source: "registry"                     # registry | explicit
  top_n: 20                              # source=registry: pick top N by IC
  min_ic: 0.02                           # minimum IC threshold
  exclude: ["fwd_ret_5d", "fwd_ret_20d"] # columns to exclude

hyperparams:
  search_space:                          # Optuna trial generation
    num_leaves: {type: int, low: 15, high: 127}
    learning_rate: {type: loguniform, low: 0.001, high: 0.1}
    feature_fraction: {type: uniform, low: 0.5, high: 1.0}
    bagging_fraction: {type: uniform, low: 0.5, high: 1.0}
    min_child_samples: {type: int, low: 10, high: 200}
    lambda_l2: {type: loguniform, low: 1e-8, high: 10.0}
  fixed:
    objective: "regression"
    metric: "rmse"
    boosting: "gbdt"
    early_stopping_rounds: 100
    random_state: 42
    verbose: -1

data:
  dataset: "us_top20_v1"                 # DatasetManager.load() key
  label: "fwd_ret_5d"

training:
  optuna_direction: "maximize"           # maximize IC or minimize rmse
  optuna_metric: "val_ic"               # val_ic | val_rmse
  n_trials: 50
  cv_folds: 5
```

### 4.3 ModelRegistry (`ml/registry.py`)

```python
import mlflow
import mlflow.lightgbm
from dataclasses import dataclass

@dataclass
class ModelBundle:
    """Unified return type for all model consumers."""
    name: str
    version: int
    model: object               # lgb.Booster | sklearn Ridge | ...
    config: dict                # Full config.yaml content
    features: list[str]         # Feature column names
    metadata: dict              # metrics, dataset, git_commit, trained_at

class ModelRegistry:
    """MLflow Tracking + Model Registry wrapper."""

    @classmethod
    def save(cls, name: str, model, config: dict, metrics: dict,
             features: list[str], dataset_name: str, artifacts: dict = None) -> int:
        """Save model to MLflow. Returns version number.
        
        Auto-records: config.yaml, model, metrics, feature list,
        dataset version, git commit, training timestamp.
        """

    @classmethod
    def load(cls, name: str, version: int | str = "latest") -> ModelBundle:
        """Load a registered model by name and version."""

    @classmethod
    def list_versions(cls, name: str) -> list[dict]:
        """List all versions for a model: {version, metrics, stage, created_at}."""

    @classmethod
    def promote(cls, name: str, version: int, stage: str):
        """Transition model stage: staging → production → archived."""

    @classmethod
    def get_latest_version(cls, name: str, stage: str = "production") -> int:
        """Get the latest version number in a given stage."""
```

### 4.4 OptunaTuner (`ml/tuner.py`)

```python
class OptunaTuner:
    """Hyperparameter tuning with Optuna, integrated with ModelRegistry."""

    def __init__(self, config_path: str):
        """Load model config.yaml."""

    def tune(self, n_trials: int = None) -> ModelBundle:
        """Run Optuna study, save best model to ModelRegistry.
        
        Returns the best ModelBundle (already saved to MLflow).
        """
```

Flow:
1. Load `config.yaml` → parse `hyperparams.search_space`
2. Load dataset via `DatasetManager.load(config.data.dataset)`
3. For each trial: suggest params → `ModelTrainer` train → evaluate on val set
4. Log trial metrics to MLflow (nested run under parent experiment)
5. Best trial → `ModelRegistry.save()` → promote to "production"
6. Return `ModelBundle`

### 4.5 Model Consumers — Unified Loading

One place to change: `strategies/ml_pred.py`.

```python
# BEFORE (current):
class MLPredStrategy:
    def _train(self, symbols):
        trainer = ModelTrainer(factor_path=None)
        trainer.load_from_bq(symbols, self.train_start, self.train_end)
        result = trainer.train_lightgbm()
        self._model = result["model"]
        self._model_trainer = trainer

# AFTER (new):
class MLPredStrategy:
    def _load_model(self):
        try:
            bundle = ModelRegistry.load(self.model_name, self.model_version)
            self._model = bundle.model
            self._config = bundle.config
            self._features = bundle.features
            logger.info("Loaded %s v%d (%d features, IC=%.4f)",
                        bundle.name, bundle.version,
                        len(bundle.features), bundle.metadata.get("val_ic", 0))
        except Exception:
            logger.exception("Failed to load model, falling back to live training")
            self._fallback_train()
```

All other consumers (PaperRunner, LiveRunner, W2/W3 scripts) benefit automatically since they all go through MLPredStrategy.

---

## 5. DatasetManager (`ml/datasets.py`)

### 5.1 Dataset Naming

```
{market}_{scope}_v{version}

Examples:
  us_top20_v1      # US top 20 stocks
  us_all_v1        # All 234 US stocks
  hk_all_v1        # All 15 HK stocks
```

### 5.2 Storage (GCS)

```
gs://deductive-notch-495015-c2-quant-data/datasets/
  us_top20_v1/
    train.parquet
    val.parquet
    test.parquet
    meta.json
```

### 5.3 meta.json

```json
{
  "name": "us_top20_v1",
  "created_at": "2026-06-01T08:00:00Z",
  "market": "us",
  "symbols": ["AAPL","MSFT","NVDA","AMZN","GOOGL","META","TSLA","AMD","NFLX","INTC"],
  "n_symbols": 20,
  "features": ["mom_20d","vol_20d","pe_ttm"],
  "n_features": 45,
  "label": "fwd_ret_5d",
  "train_range": ["2020-01-01", "2023-12-31"],
  "val_range":   ["2024-01-01", "2024-12-31"],
  "test_range":  ["2025-01-01", "2025-12-31"],
  "train_rows": 156000,
  "val_rows": 41000,
  "test_rows": 41000,
  "factor_computed_at": "2026-06-01T07:00:00Z",
  "git_commit": "a280d79"
}
```

### 5.4 API

```python
@dataclass
class DatasetConfig:
    market: str
    symbols: list[str] | str    # list or "all"
    features: list[str] | str   # explicit list or "from_registry_top_N"
    label: str
    train_range: tuple[str, str]
    val_range: tuple[str, str]
    test_range: tuple[str, str]

@dataclass
class DatasetBundle:
    name: str
    meta: dict
    train: pd.DataFrame
    val: pd.DataFrame
    test: pd.DataFrame

class DatasetManager:

    @classmethod
    def create(cls, name: str, config: DatasetConfig) -> str:
        """Query BQ factor_values → split by date → write GCS Parquet.
        Returns dataset name."""

    @classmethod
    def load(cls, name: str) -> DatasetBundle:
        """Load train/val/test from GCS. Returns DatasetBundle."""

    @classmethod
    def list_all(cls) -> list[dict]:
        """List all datasets with metadata summaries."""

    @classmethod
    def exists(cls, name: str) -> bool:
        """Check if dataset exists in GCS."""
```

### 5.5 Link to Model Training

```yaml
# models/momentum_lgbm/v3/config.yaml
data:
  dataset: "us_top20_v1"    # ← DatasetManager.load() key
```

`ModelRegistry.save()` auto-records:
```python
mlflow.log_param("dataset", config["data"]["dataset"])
mlflow.log_param("features", features)
```

---

## 6. End-to-End Workflow

```bash
# 1. Create dataset (one-time)
python -m ml.datasets create us_top20_v1 \
  --symbols top20 --label fwd_ret_5d \
  --train 2020-01-01 2023-12-31 \
  --val 2024-01-01 2024-12-31 \
  --test 2025-01-01 2025-12-31

# 2. Tune model (repeatable)
python -m ml.tuner models/momentum_lgbm/v3/config.yaml --trials 50

# 3. Paper trading validation
python -m live.run --mode paper --config live/paper_us.yaml

# 4. Live trading
python -m live.run --mode live --config live/live_us.yaml

# 5. Model upgrade cycle
python -m ml.tuner models/momentum_lgbm/v4/config.yaml --trials 100
python -m live.run --mode paper --model-name momentum_lgbm --model-version 4
# If paper passes:
python -m live.run --mode live --model-version 4
```

---

## 7. Implementation Phases

### Phase 1: ML Infrastructure (no dependencies)

| # | Task | File(s) | Notes |
|---|------|---------|-------|
| 1.1 | DatasetManager | `ml/datasets.py` | BQ → GCS Parquet + meta.json + load/list |
| 1.2 | ModelRegistry | `ml/registry.py` | MLflow Tracking + Registry integration |
| 1.3 | OptunaTuner | `ml/tuner.py` | Parse config.yaml search_space → tune → auto-save |
| 1.4 | Model configs | `models/momentum_lgbm/v1/config.yaml` | Factor, hyperparam, data blocks |
| 1.5 | Create initial datasets | `us_top20_v1`, `us_all_v1` | Script calling DatasetManager.create() |

### Phase 2: Live Loop (depends on Phase 1)

| # | Task | File(s) | Notes |
|---|------|---------|-------|
| 2.1 | LiveRunner main loop | `live/runner.py` | Data→Strategy→OMS→Dashboard |
| 2.2 | CLI + config | `live/run.py` + `live/config.yaml` | `--mode paper|liquid` |
| 2.3 | Observer | `live/observer.py` | Logs, snapshots, trade records |
| 2.4 | Reporter | `live/reporter.py` | HTML + matplotlib charts |
| 2.5 | Dashboard WebSocket | `dashboard/api.py` extend | Real-time state push |

### Phase 3: Integration & Verification

| # | Task | Content |
|---|------|---------|
| 3.1 | MLPredStrategy → Registry | `_train()` → `_load_model()` with fallback |
| 3.2 | Paper mode E2E | Full trading day paper run |
| 3.3 | Live mode connectivity | FutuStockBroker test (no real orders) |
| 3.4 | Model upgrade cycle | Optuna tune → save → paper verify → live switch |

---

## 8. Key Design Decisions

1. **Paper/Live share Broker protocol** — `PaperBroker` and `FutuStockBroker` implement the same abstract interface. LiveRunner doesn't care which one it's using.

2. **Model config per version** — Each model version has its own `config.yaml`. Factors, hyperparameters, and data are self-contained. Reproducible by design.

3. **Dataset as pre-computed artifact** — Stored as Parquet in GCS. Models reference datasets by name. Model → dataset lineage is recorded in MLflow.

4. **Fallback strategy** — If `ModelRegistry.load()` fails (missing model, network error), MLPredStrategy falls back to live training from BQ. LiveRunner config can also specify a `fallback_strategy` (e.g., SimpleMomentum).

5. **Observer is passive** — Doesn't affect the trading loop. Records are written asynchronously where possible. A failure in Observer never kills the main loop.

6. **Existing code untouched** — `run_paper.py`, collectors, factor pipeline, and BQ loader remain unchanged. New code in `live/` and `ml/` is additive.

---

## 9. File Change Summary

| Status | File | Change |
|--------|------|--------|
| NEW | `live/runner.py` | LiveRunner main loop |
| NEW | `live/observer.py` | Observer: logs, snapshots, records |
| NEW | `live/reporter.py` | HTML report + charts |
| NEW | `live/config.py` | Config loader + validation |
| NEW | `live/run.py` | CLI entry point |
| NEW | `live/__init__.py` | Package init |
| NEW | `ml/datasets.py` | DatasetManager |
| NEW | `ml/registry.py` | ModelRegistry |
| NEW | `ml/tuner.py` | OptunaTuner |
| NEW | `models/momentum_lgbm/v1/config.yaml` | Model config |
| MODIFY | `strategies/ml_pred.py` | `_train()` → `_load_model()` with fallback |
| MODIFY | `dashboard/api.py` | Add WebSocket endpoint |
| MODIFY | `ml/__init__.py` | Export new classes |
| UNCHANGED | `run_paper.py`, `ml/trainer.py`, collectors, BQ loader, cron, factor pipeline | — |
