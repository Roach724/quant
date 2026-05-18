# Factor + ML + ExperimentTracker 设计文档

> Date: 2026-05-18
> Author: Jarvis
> Status: Draft
> Related: hk-quant 回填 → quant 移植

---

## 1. 总体架构

```
┌────────────────────────────────────────────────────────────┐
│                  量化策略开发流水线                          │
├──────────┬───────────┬────────────┬────────────────────────┤
│ Phase 1  │ Phase 2  │  Phase 3   │     Phase 4            │
│ 因子构建  │ ML 训练   │  回测验证   │   实盘跟踪             │
├──────────┼───────────┼────────────┼────────────────────────┤
│ Factor   │ Model     │ Engine +   │ ExperimentTracker      │
│ Builder  │ Trainer   │ WalkForward│ + InvestmentRecord     │
└──────────┴───────────┴────────────┴────────────────────────┘
           ↑                  ↑
     独立模块           零改动接收
```

**核心原则**：FactorBuilder 和 ModelTrainer 完全不依赖 Engine。Engine 通过 `bar_data["pred"]` 获取预测值，**对 ML 一无所知**。

---

## 2. FactorBuilder — 独立因子模块

### 模块位置

```
quant/
├── engine/
│   ├── factors.py         ← 保留现有 Factor dataclass + compute_ic
│   └── ...         
├── factors/               ← NEW 独立因子模块
│   ├── __init__.py
│   ├── builder.py         ← FactorBuilder 主类 (从 hk-quant 移植)
│   ├── alpha158.py        ← Alpha158 等价因子族
│   └── hk_specific.py     ← 港股特色因子
│   tests/
│       └── test_builder.py
```

### 与 engine/factors.py 的关系

| 文件 | 职责 | 依赖方向 |
|------|------|----------|
| `engine/factors.py` | Factor dataclass, compute_ic, factor_returns (回测内使用) | 无变化 |
| `factors/builder.py` | FactorBuilder: 批量计算 43+ 因子, process_factors, IC 分析 | 可独立于 Engine 运行 |
| 用户代码 | `fb = FactorBuilder(); df = fb.build_factor_dataset(symbols)` | 产出标准 DataFrame → 供 ModelTrainer |

**不重复**：`engine/factors.py` 的回测内 IC 计算保留，`factors/builder.py` 的 IC 分析是批量/探索性质的。

### Factor 清单 (43+)

| 类别 | 因子 | 数量 |
|------|------|------|
| 多周期收益率 | ret_1d/5d/10d/20d/60d/120d | 6 |
| 多周期波动率 | vol_5d/10d/20d/60d | 4 |
| 成交量因子 | vol_ratio_5d/20d, corr_vp_20d, vol_trend | 4 |
| 动量/技术 | rsi_14, macd, macd_signal, macd_hist, bb_position, bb_width, price_position_20d, streak | 8 |
| 换手率 | avg_turnover_5d/20d, turnover_ratio, turnover_growth | 4 |
| 价格形态 | daily_range, upper/lower_shadow_ratio, gap, vp_divergence | 5 |
| 高阶矩 | skew_20d/60d/120d, kurt_20d/60d/120d | 6 |
| 港股特色 | low_vol_proxy, price_stability | 2 |
| 公司行为 | (corp_action_factors, 预留接口) | ~4 |
| **总计** | | **~43** |

### 核心接口

```python
class FactorBuilder:
    def build_factor_dataset(self, symbols, start, end) -> pd.DataFrame
    def process_factors(self, factor_df, winsor_pct=0.01) -> pd.DataFrame
    def compute_ic(self, factor_df, label_col="fwd_ret_5d") -> pd.DataFrame
    def ic_summary(self, ic_df) -> pd.DataFrame
    def factor_correlation(self, factor_df) -> pd.DataFrame
    def save_factors(self, factor_df, path)
```

---

## 3. ML 集成（方案 A）— 预测值作为数据列

### 数据流

```
┌──────────┐   因子矩阵    ┌──────────┐   预测值    ┌──────────┐
│ Factor   │ ──────────→  │ Model    │ ─────────→ │ pred df  │
│ Builder  │  (T×N×F)    │ Trainer  │  (T×N)    │          │
└──────────┘              └──────────┘            └────┬─────┘
                                                       │
                                                       ▼
┌─────────────────────────────────────────────────────────────┐
│ DataFrameSource(close=..., pred=pred_df)                     │
│   → data.iloc(bar) → {"close": {...}, "pred": {...}}         │
└─────────────────────────────────────────────────────────────┘
                                                       │
                                          ┌────────────┴────────────┐
                                          ▼                        ▼
                           Strategy.on_bar(ctx, bar)         RiskRule.apply()
                           读取 bar_data["pred"]             可能使用 pred
```

### 对 Engine 的改动（最小化）

#### 3.1 DataFrameSource 扩展

```python
class DataFrameSource:
    def __init__(self, close, open=None, high=None, low=None, volume=None, pred=None):
        self.close = close
        self.open = open if open is not None else close.copy()
        self.high = high if high is not None else close.copy()
        self.low = low if low is not None else close.copy()
        self.volume = volume if volume is not None else pd.DataFrame(1, ...)
        self.pred = pred                      # NEW: optional prediction DataFrame
        self.universe = list(close.columns)
        self.timestamp = close.index

    def iloc(self, i):
        row = {"close": {}}
        for col in self.universe:
            row["close"][col] = self.close.iloc[i][col]
        if self.pred is not None:
            row["pred"] = {}                  # NEW: add prediction to bar_data
            for col in self.universe:
                if col in self.pred.columns:
                    row["pred"][col] = self.pred.iloc[i][col]
        return row
```

#### 3.2 StrategyContext 新增辅助属性

```python
class StrategyContext:
    @property
    def predictions(self) -> dict | None:
        """Last bar's predictions: {symbol: score}. Returns None if no ML."""
        # 缓存最近一次 bar_data 的 pred
        return self._last_pred

    def _set_bar_data(self, bar_data):
        self._last_pred = bar_data.get("pred") if bar_data else None
```

#### 3.3 Engine 改动（零行核心逻辑）

```python
# Engine.run() — 只加一行状态传递，不改变回测流程
for bar in range(n_bars):
    signals = strategy.on_bar(ctx, bar)
    if signals:
        bar_data = data.iloc(bar)
        ctx._set_bar_data(bar_data)          # ← 唯一新增
        orders = self._signals_to_orders(...)
        ...
```

#### 3.4 WalkForward 适配

```python
def _slice(self, start, end):
    args = {"close": self.data.close.iloc[start:end].copy()}
    if hasattr(self.data, 'pred') and self.data.pred is not None:
        args["pred"] = self.data.pred.iloc[start:end].copy()
    return DataFrameSource(**args)
```

### 改动汇总

| 文件 | 改动行数 | 影响范围 |
|------|---------|---------|
| `engine/data.py` | +15 行 | DataFrameSource 接受 pred, iloc 返回 pred |
| `engine/strategy.py` | +8 行 | StrategyContext 新增 predictions 属性 |
| `engine/engine.py` | +1 行 | run() 中 ctx._set_bar_data(bar_data) |
| `engine/walkforward.py` | +4 行 | _slice 传递 pred |
| `engine/__init__.py` | +0 行 | 只导出已有接口 |
| `engine/tests/test_data.py` | +15 行 | 测试 pred 数据流 |
| **总计** | **~43 行** | **全部向后兼容** |

---

## 4. ModelTrainer — ML 训练模块

### 模块位置

```
quant/
├── ml/                       ← NEW
│   ├── __init__.py
│   ├── trainer.py            ← ModelTrainer (OLS/Ridge/LightGBM)
│   ├── evaluate.py           ← IC 评估, 分层回测
│   └── tests/
│       └── test_trainer.py
```

### 核心接口

```python
class ModelTrainer:
    def load_data(self, factor_path) -> pd.DataFrame         # 加载因子
    def split_data(self, train_end, val_end, test_end)       # 时序划分
    def train_ols(self, train, val) -> dict                  # 基线
    def train_ridge(self, train, val, alpha=1.0) -> dict     # Ridge
    def train_lightgbm(self, train, val) -> dict             # 主力模型
    def evaluate_ic(self, model, df) -> dict                 # Rank IC
    def predict(self, model, df) -> pd.Series                # 批量预测
    def save_model(self, model, path)                        # 保存
    def load_model(self, path) -> object                     # 加载
```

### 模型决策树

```
                新因子数据集
                    │
                    ▼
               OLS 基线训练
                    │
          ┌─────────┴─────────┐
          ▼                    ▼
      RMSE 合理?          RMSE 太差?
          │                    │
          ▼                    ▼
      Ridge 调优         回退检查因子质量
          │                    │
          ▼                    ▼
      LightGBM            (因子预处理改善后重来)
      (early stopping)
          │
          ▼
      IC 评估 (Rank IC + ICIR)
          │
          ▼
      分层回测验证 (BacktestRunner)
```

---

## 5. ExperimentTracker — 实验管理器

### 模块位置

```
quant/
├── experiment/                ← NEW
│   ├── __init__.py
│   ├── tracker.py             ← ExperimentTracker
│   ├── investment_record.py   ← InvestmentRecord (投资档案)
│   └── runner.py              ← ExperimentRunner (流水线编排)
│   tests/
│       └── test_tracker.py
```

### 目录结构

```
data/experiments/
├── INDEX.md                      ← 实验总表
├── exp_001_baseline/
│   ├── experiment.json           ← 元数据 + git 信息 + 结果
│   ├── investment_sessions.json  ← 关联的投资会话索引
│   └── results/
│       └── report.html           ← 回测报告
└── exp_002_new_factors/
    └── ...
```

### 核心接口

```python
class ExperimentTracker:
    def register_experiment(self, exp_id, name, hypothesis, changes)
    def update_results(self, exp_id, results, verdict)        # 更新结果
    def record_session(self, exp_id, session_id, type, path)  # 关联投资会话
    def get_experiment(self, exp_id) -> dict
    def compare(self, exp_id_a, exp_id_b) -> str              # Markdown 对比报告
    def list_experiments(self) -> list[dict]

class InvestmentRecord:
    def __init__(self, strategy_name)
    def set_config(self, config)
    def record_trade(self, time, symbol, side, qty, price, cost)
    def record_signal(self, date, symbol, score, rank)
    def record_risk_event(self, event_type, detail)
    def save(self, output_dir)                                # 保存完整投资档案
    def generate_summary(self) -> str                         # 人类可读报告

class ExperimentRunner:
    def run_full_experiment(self, exp_id, name, hypothesis,
                            skip_training=False, ...) -> dict  # 一键运行
```

### 投资档案保存结构

```
data/investments/{session_id}/
├── meta.json              — 元数据 + 配置快照
├── performance.json       — 绩效汇总 (Sharpe/Return/DD/...)
├── trades.csv             — 成交明细
├── daily_pnl.csv          — 每日权益曲线
├── positions_final.csv    — 最终持仓
├── risk_events.csv        — 风控事件
├── signal_log.csv         — 信号排名
└── summary_report.txt     — 人类可读报告
```

---

## 6. 数据流总览

```
                     FactorBuilder (离线/批量，一劳永逸)
                     │
                     ▼ 因子矩阵 (T×N×F)
              ModelTrainer.train()
                     │
                     ▼ LightGBM model
              ModelTrainer.predict(model, df)
                     │
                     ▼ pred_df (T×N)  ← 一行预测值 per stock per day
                     │
              ┌──────┴──────┐
              │              │
              ▼              ▼
    DataFrameSource    ExperimentTracker
    (pred=pred_df)     (记录实验参数)
              │
              ▼
       Engine.run(strategy, data)
         └── strategy.on_bar(ctx, bar)
               └── ctx.predictions[symbol] → 决策
              │
              ▼
        Result + Metrics
              │
              ▼
    InvestmentRecord.save()
              │
              ▼
    ExperimentTracker.update_results()
```

---

## 7. 向后兼容矩阵

| 已有特性 | 改动后是否有影响 | 理由 |
|----------|----------------|------|
| Engine.run() | ❌ 无 | 核心循环不变 |
| Strategy 类 | ❌ 无 | on_bar 签名不变，不读 pred 的策略照跑 |
| RiskRule 接口 | ❌ 无 | bar_data dict 格式不变 |
| GridSearch | ❌ 无 | 只调 Engine.run |
| WalkForward | ❌ 无 | _slice 补充 pred 参数，无 pred 时行为不变 |
| Report | ❌ 无 | 只读 portfolio |
| Metrics | ❌ 无 | 只读 equity curve |
| 现有测试 | ❌ 无 | 测试用的 bar_data = {"close": ...} 格式不变 |
| DataSource Protocol | ❌ 无 | iloc 返回类型不变（dict），只是多了 key |

---

## 8. 实施顺序

```
Phase A: FactorBuilder 移植
  1.1 创建 factors/ 目录 + 测试框架
  1.2 移植 FactorBuilder 核心 (alpha158 + HK)
  1.3 创建 process_factors pipeline
  1.4 移植 IC 分析
  1.5 集成测试：用现有 HK 数据跑通

Phase B: DataFrameSource 扩展 + 测试
  2.1 修改 data.py (pred 参数 + iloc)
  2.2 更新 StrategyContext (predictions 属性)
  2.3 Engine +1 行 (ctx._set_bar_data)
  2.4 WalkForward 适配
  2.5 测试覆盖率 100%

Phase C: ModelTrainer 移植
  3.1 创建 ml/ 目录
  3.2 移植 ModelTrainer (OLS/Ridge/LightGBM)
  3.3 移植 evaluate.py (IC/分位数回测)
  3.4 端到端测试：FactorBuilder → ModelTrainer → Engine

Phase D: ExperimentTracker
  4.1 创建 experiment/ 目录
  4.2 移植 ExperimentTracker
  4.3 移植 InvestmentRecord
  4.4 移植 ExperimentRunner
  4.5 集成到端到端流水线
```
