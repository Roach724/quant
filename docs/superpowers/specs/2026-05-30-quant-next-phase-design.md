# Quant 下一阶段设计 — Paper Runner 渐进验证 + 因子注册制

> 日期: 2026-05-30 · 状态: Draft · 作者: Jarvis + 老大

---

## 1. 目标

**主线**：验证动量策略和 LightGBM 策略在美股的有效性，决定是否上实盘。  
**支线**：建立因子注册制，解耦因子生产和消费。

```
数据就绪 ──→ W1 动量验证 ──→ 因子库 ──→ W2 ML 对比 ──→ W3 5m ──→ W4 决策
               │                         ▲
               └── 全链路打通             │
                                  FactorRegistry (BQ 双表)
```

**成功标准**：4 周后 ML 策略 paper 表现与回测衰减 < 20%（Sharpe），优于动量基准。

---

## 2. 设计原则

### 2.1 复用优先
不改写已有组件，只新增策略类、配置文件、调度脚本。

### 2.2 多市场兼容（硬约束）
所有策略/配置通过 `market` 参数区分市场，不硬编码标的。

```python
class MomentumStrategy:
    DEFAULT_CONFIG = {
        "us": {"lookback": 20, "top_k": 20},
        "hk": {"lookback": 20, "top_k":  5},
    }
```

### 2.3 因子解耦
策略通过 FactorRegistry 挑因子，不直接调 FactorBuilder。

---

## 3. Factor Registry（W2 前置）

### 3.1 架构

```
因子生产                     因子库                        因子消费
FactorBuilder               BQ 双表                      策略 / ML
    ├─ 注册 ────────────→│ registry     │←── 查询有效因子 ───┤
    │                    │ (元数据)      │                    │
    ├─ 评估 ────→ factor_evaluations ───┤                    │
    │               │  (评估快照)  │     │                    │
    │               └── 准入门槛 ──┘     │                    │
    │               IC>0.05, t-stat>3    │                    │
```

### 3.2 Schema

**factor_registry**: factor_id, name, market, category, source, formula, is_active, latest_ic_mean/tstat/coverage, tags  
**factor_evaluations**: eval_id, factor_id, ic_mean/std/tstat/ir, ic_decay_1d/5d/20d, coverage, skew, kurt, passes_admission, admission_details

两张表均 PARTITION BY DATE(…) CLUSTER BY …

### 3.3 准入标准

| 标准 | 阈值 | 不通过 => |
|------|------|-----------|
| Rank IC (abs) | > 0.05 | is_active=false |
| IC t-statistic | > 3.0 | is_active=false |
| 覆盖率 | > 90% | is_active=false |
| IC 衰减 20d | 不反转 | warn only |
| 最大相关性 | < 0.7 | warn only |

### 3.4 API

FactorRegistry.register() / .evaluate() / .get_active(market) / .deactivate(id) / .get_history(id)

### 3.5 集成

- FactorBuilder 加 compute(factor_names, data) 方法
- ModelTrainer 从 registry.get_active("us") 取因子
- ExperimentTracker 记录使用的 factor_ids

---

## 4. Paper Runner 四阶段

### 4.1 W1: 日线动量 — 全链路验证

BQ us_bars_1d ──→ PaperRunner BQ loader ──→ SimpleMomentum (已有) ──→ Broker → OMS → Risk → Report

SimpleMomentum（已有）: 过去 20 日收益率排名，top-K 等权做多，每 5 日调仓。

| # | 步骤 | 产出 |
|---|------|------|
| 1.1 | PaperRunner 加 BQ 直接数据源 | _sdk_data() 改查 BQ OHLCV |
| 1.2 | 验证 SimpleMomentum | 已有，无需改 |
| 1.3 | 启动脚本 run_paper_momentum.sh | 一键跑 |
| 1.4 | 跑 2026-01-01 → 至今 | metrics + HTML report |
| 1.5 | 验证全链路 | 订单/成交/持仓/风控 |

### 4.2 W2: 因子库 + ML

| # | 步骤 | 产出 |
|---|------|------|
| 2.1 | Factor Registry 实施 | BQ 双表 + API |
| 2.2 | 写 MLPredStrategy | registry 取因子 → LightGBM → 选股 |
| 2.3 | Walk-forward 配置 | 2020-2024 train, 2025 val, 2026 test |
| 2.4 | ExperimentRunner 双策略对比 | IC/Sharpe/MaxDD |
| 2.5 | Paper Runner 双策略并行 | 对比日志 |

### 4.3 W3: 5 分钟 + Cron

| # | 步骤 |
|---|------|
| 3.1 | 5m walk-forward |
| 3.2 | 参数调优 |
| 3.3 | Paper Runner cron 定时 |
| 3.4 | 观察 3+ 交易日 |

### 4.4 W4: 评估 & 决策

| 指标 | 容忍衰减 |
|------|----------|
| Sharpe | < 20% |
| MaxDD | < 25% |
| 胜率 | < 10% |

衰减可接受？ → ML>动量 → Live Loop / ML≈动量 → 优化 / 衰减严重 → 排查

---

## 5. 新增/修改文件

| 文件 | 用途 | 状态 |
|------|------|------|
| `sql/factor_registry_schema.sql` | BQ 建表 DDL | 新增 |
| `factors/registry.py` | FactorRegistry 类 | 新增 |
| `factors/evaluation.py` | 因子评估逻辑 | 新增 |
| `scripts/init_factor_registry.py` | 初始化注册库 | 新增 |
| `scripts/run_paper_momentum.sh` | W1 启动脚本 | 新增 |
| `strategies/ml_pred.py` | W2 ML 策略 | 新增 |
| `factors/builder.py` | +compute() 方法 | 修改 |
| `run_paper.py` | +BQ 数据源 | 修改 |

---

## 6. 风险

| 风险 | 缓解 |
|------|------|
| BQ 数据延迟/缺数据 | 回填 at-job 确保就绪 |
| ML 过拟合 paper 衰减 | Walk-forward 设计 |
| 准入过严 active 因子少 | 阈值可配 |
