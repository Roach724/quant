# 实施计划 — Factor + ML + ExperimentTracker

> Date: 2026-05-18
> 基于设计文档: docs/plans/2026-05-18-factor-ml-experiment-design.md
> 每个任务 ~2-5 分钟，TDD 驱动

---

## Phase A: FactorBuilder 移植 (5 tasks)

### Task A1: 创建 factors/ 模块骨架

**文件**: `quant/factors/__init__.py`

内容:
```python
from factors.builder import FactorBuilder
```

**验证**: `python -c "from factors import FactorBuilder"`

---

### Task A2: 移植 FactorBuilder 核心

**文件**: `quant/factors/builder.py`

移植自 `hk-quant/src/factor_builder.py`，包含：
- 所有因子计算方法 (returns, volatility, volume, momentum, turnover, price_patterns, skew_kurt, hk_dividend_yield)
- `compute_factors(df)` — 对单只股票计算全部因子
- `process_factors(df)` — winsorization + z-score + NaN fill
- `build_factor_dataset(symbols, start, end)` — 批量处理

**改动 vs hk-quant 原版**：
- 移除对 `HKDataPipeline` 的直接依赖 → 接收标准 OHLCV DataFrame
- `build_factor_dataset` 改为接收 `data_loader: Callable[[str], pd.DataFrame]` 回调，而不是硬编码 `pipeline.load_parquet`
- 保留全部 43+ 因子计算逻辑

**测试**: `quant/factors/tests/test_builder.py`
- 用合成 OHLCV 数据验证每个因子族的输出
- 验证 process_factors 的去极值和标准化效果
- 验证 compute_ic 的返回格式

**验证**: `pytest factors/tests/ -v`

---

### Task A3: 移植 IC 分析

**文件**: `quant/factors/builder.py` (追加方法)

- `compute_ic(factor_df, label_col)` — 截面 Rank IC
- `ic_summary(ic_df)` — IC 汇总统计
- `factor_correlation(factor_df)` — 因子间相关性矩阵

**测试**: 
- 用已知数据集验证 IC 计算正确性
- 验证 ic_summary 排序

---

### Task A4: 移植 save/load

**文件**: `quant/factors/builder.py` (追加方法)

- `save_factors(factor_df, path)`
- `load_factors(path)` — classmethod

**测试**:
- 保存后重新加载，验证 shape 和数据一致性

---

### Task A5: 端到端集成测试

**文件**: `quant/factors/tests/test_integration.py`

用现有 HK 日线数据验证完整链路：
```
load_parquet("daily", "0700") 
→ compute_factors() 
→ process_factors() 
→ compute_ic()
→ ic_summary()
```

**验证**: `pytest factors/tests/test_integration.py -v`

---

## Phase B: DataFrameSource 扩展 + Engine 适配 (4 tasks)

### Task B1: DataFrameSource 添加 pred 支持

**文件**: `quant/engine/data.py`

改动：
- `__init__`: 接受可选 `pred: pd.DataFrame | None`
- `iloc()`: 当 `self.pred is not None` 时，返回 `row["pred"] = {...}`

**测试**: `quant/engine/tests/test_data.py`
- 测试 `DataFrameSource(close=..., pred=pred_df)` 初始化
- 测试 `iloc(i)` 返回 `{"close": {...}, "pred": {...}}`
- 测试 `pred=None` 时行为不变（向后兼容）

**验证**: `pytest engine/tests/test_data.py -v`

---

### Task B2: StrategyContext 新增 predictions 辅助属性

**文件**: `quant/engine/strategy.py`

改动：
- StrategyContext 新增 `_last_pred: dict | None = None`
- 新增 `_set_bar_data(bar_data)` 方法
- 新增 `predictions` property → 返回 `self._last_pred`

**测试**: `quant/engine/tests/test_strategy.py`
- 创建 StrategyContext，调用 `_set_bar_data({"close": {...}, "pred": {"AAPL": 0.8}})`
- 验证 `ctx.predictions == {"AAPL": 0.8}`
- 验证不调用 `_set_bar_data` 时 `ctx.predictions is None`

**验证**: `pytest engine/tests/test_strategy.py -v`

---

### Task B3: Engine.run() +1 行

**文件**: `quant/engine/engine.py`

改动：
- `run()` 循环中，获取 `bar_data` 后调用 `ctx._set_bar_data(bar_data)`

**测试**: `quant/engine/tests/test_engine.py`
- 写一个 ML 策略：在 on_bar 中读取 `ctx.predictions`，score > 0.5 时买入
- DataFrameSource 传入 pred 数据
- 验证策略能正确读取预测值并生成信号
- 验证不带 pred 的原策略仍然正确运行

**验证**: `pytest engine/tests/test_engine.py -v`

---

### Task B4: WalkForward 适配

**文件**: `quant/engine/walkforward.py`

改动：
- `_slice()`: 如果 data 有 pred 属性且在 slice 范围内，传递给 DataFrameSource

**测试**: `quant/engine/tests/test_walkforward.py`
- 用带 pred 的数据运行 WalkForward
- 验证每折的 test_data 包含 pred

**验证**: `pytest engine/tests/test_walkforward.py -v`

---

## Phase C: ModelTrainer 移植 (3 tasks)

### Task C1: 创建 ml/ 模块骨架

**文件**: `quant/ml/__init__.py`

依赖: `scikit-learn`, `lightgbm`, `scipy`

**验证**: `pip install lightgbm && python -c "from ml.trainer import ModelTrainer"`

---

### Task C2: 移植 ModelTrainer

**文件**: `quant/ml/trainer.py`

移植自 `hk-quant/src/model_trainer.py`，包含：
- `load_data(factor_path)` / `split_data(train_end, val_end, test_end)`
- `train_ols(train, val)` / `train_ridge(train, val, alpha)`
- `train_lightgbm(train, val)` — purged TS-CV, early stopping
- `evaluate_ic(model, df)` — Rank IC + ICIR
- `predict(model, df)` → pd.Series (flat, per-row prediction)

**改动 vs hk-quant 原版**：
- 不再硬编码 `self.feature_cols` 的排除列表，改为从 factor_df 的 metadata 推断
- 移除 hk-quant-specific 的数据加载路径硬编码
- scikit-learn scaler 改为可选（树模型不用标准化）

**测试**: `quant/ml/tests/test_trainer.py`
- 用合成因子数据训练 OLS/Ridge/LightGBM
- 验证 predict 输出维度正确
- 验证 evaluate_ic 返回格式

**验证**: `pytest ml/tests/ -v`

---

### Task C3: End-to-end ML → Engine 集成测试

**文件**: `quant/ml/tests/test_integration.py`

完整链路：合成因子数据 → ModelTrainer → predict → DataFrameSource(pred=...) → Engine.run() → 验证策略根据预测值买卖

**验证**: `pytest ml/tests/test_integration.py -v`

---

## Phase D: ExperimentTracker (4 tasks)

### Task D1: 创建 experiment/ 模块骨架

**文件**: `quant/experiment/__init__.py`

**验证**: `python -c "from experiment.tracker import ExperimentTracker"`

---

### Task D2: 移植 ExperimentTracker

**文件**: `quant/experiment/tracker.py`

移植自 `hk-quant/src/experiment_tracker.py`：
- `register_experiment(exp_id, name, hypothesis, changes)`
- `update_results(exp_id, results, verdict)`
- `record_session(exp_id, session_id, type, path)`
- `get_experiment(exp_id)` / `list_experiments()`
- `compare(exp_id_a, exp_id_b)` → Markdown 报告

**改动 vs hk-quant 原版**：
- INDEX.md 路径改为 `data/experiments/`
- git 信息获取改为相对当前 workspace

**测试**: `quant/experiment/tests/test_tracker.py`
- 注册实验 → 验证 JSON 文件生成
- 更新结果 → 验证文件内容
- 对比两个实验 → 验证 Markdown 报告格式

**验证**: `pytest experiment/tests/ -v`

---

### Task D3: 移植 InvestmentRecord

**文件**: `quant/experiment/investment_record.py`

移植自 `hk-quant/src/investment_record.py`：
- `set_config(config)`
- `record_trade(time, symbol, side, qty, price, cost)`
- `record_signal(date, symbol, score, rank)`
- `record_risk_event(event_type, detail)`
- `save(output_dir)` → 完整的投资档案（meta + trades + pnl + positions + signals + risk events）
- `generate_summary()` → 人类可读报告

**测试**: 
- 记录虚拟交易/信号/风控事件 → 保存 → 验证所有文件生成
- 验证 summary_report.txt 格式

**验证**: `pytest experiment/tests/test_investment_record.py -v`

---

### Task D4: 移植 ExperimentRunner

**文件**: `quant/experiment/runner.py`

移植自 `hk-quant/src/experiment_runner.py`：
- `run_full_experiment(exp_id, name, hypothesis, ...)` → 一键式流水线

**改动 vs hk-quant 原版**：
- 适配 quant 的目录结构和模块路径
- 移除 hk-quant 的 PaperTrader/LivePaperSession 引用（暂时）
- 用 Engine.run() 替代 hk-quant 的 BacktestRunner

**测试**: 
- 运行 `run_full_experiment` 简版（仅训练+回测）
- 验证 experiment.json 和 investment 文件生成

**验证**: `pytest experiment/tests/test_runner.py -v`

---

## 时间估计

| Phase | 任务数 | 估计时间 |
|-------|--------|---------|
| A: FactorBuilder | 5 | 25-40 min |
| B: Engine 适配 | 4 | 20-30 min |
| C: ModelTrainer | 3 | 20-30 min |
| D: ExperimentTracker | 4 | 25-35 min |
| **总计** | **16** | **90-135 min** |

---

## 执行方式

选择 **Subagent 驱动（当前推荐）** 还是手动执行？

如果选 Subagent 驱动，每个任务将：
1. `sessions_spawn` implementer — TDD: 写测试→跑失败→实现→跑绿
2. `sessions_spawn` spec-reviewer — 验证代码匹配设计
3. `sessions_spawn` code-reviewer — 验证代码质量
