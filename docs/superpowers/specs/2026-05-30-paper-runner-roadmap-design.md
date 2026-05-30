# Paper Runner Roadmap — 从回测到 Live 的渐进式验证

> 日期: 2026-05-30 · 状态: Draft · 作者: Jarvis + 老大

---

## 1. 目标

验证动量因子策略和 LightGBM 预测策略在美国股票市场的有效性，通过
**回测 → walk-forward → 纸交易 → 评估** 的渐进式流程，决定是否进入 Live Trading Loop 开发。

**成功标准**：4 周后，ML 策略的 paper 表现与回测衰减 < 20%（Sharpe），且优于动量基准。

---

## 2. 设计原则

### 2.1 复用优先

不改写已有组件，只新增：策略类、配置文件、调度脚本。

| 已有组件 | 用途 |
|----------|------|
| FactorBuilder (43+ 因子) | 因子计算 |
| ModelTrainer (OLS/Ridge/LightGBM) | 模型训练 |
| ExperimentTracker | 实验记录 |
| PaperRunner | 历史回放模拟 |
| PaperBroker | 模拟成交 |
| RouterOMS | 订单路由 |
| RiskManager | 风控 |
| BQ + SDK | 数据源 |

### 2.2 多市场兼容（硬约束）

所有策略、配置、实验必须通过 `market` 参数区分市场，不得硬编码标的或市场假设。

```python
# ✅ 正确
class MomentumStrategy:
    DEFAULT_CONFIG = {
        "us": {"lookback": 20, "top_k": 20, "rebalance": "daily"},
        "hk": {"lookback": 20, "top_k":  5, "rebalance": "daily"},
        "crypto": {"lookback": 7, "top_k":  3, "rebalance": "4h"},
    }
    def __init__(self, market="us"):
        self.cfg = self.DEFAULT_CONFIG[market]
```

| 层 | 多市场兼容方式 |
|----|---------------|
| 数据源 | `market` 参数 → BQ 表路由 |
| 策略 | `market_config` dict |
| Broker/OMS | symbol 前缀路由 (HK./US./CC.) |
| 风控 | portfolio 级别，不区分 market |
| ExperimentTracker | `market` 列 |
| 调度 cron | 各市场不同收盘时间，已有 |

### 2.3 因子解耦

策略通过 **因子库**（独立 spec）挑选因子，不直接调 `FactorBuilder`。
因子生产和使用分离：研究员入库因子 → 策略员从库选因子。

> **前置依赖**：W2 启动前需完成因子库重构。详见 `docs/superpowers/specs/YYYY-MM-DD-factor-registry-design.md`（待写）。

---

## 3. 四阶段路线图

```
W1  ██ 日线动量 → 全链路验证
W2  ████ 因子库 + ML + walk-forward → 双策略对比
W3  ██ 5m 频率 + cron → 自动纸交易
W4  █ 评估 → Live or 回头
```

### 3.1 Week 1: 日线动量 — 全链路验证

**目标**：用最简单的策略验证 PaperRunner → Broker → OMS → 风控 全链路。

```
BQ us_bars_1d ──SDK──→ MomentumStrategy ──→ PaperRunner
                                    │
                              PaperBroker → RouterOMS → RiskManager
                                    │
                              持仓日志 / 盈亏报告
```

**MomentumStrategy**：
- 输入：日线 close price
- 逻辑：过去 20 日收益率排名，取 top 20 等权做多
- 调仓：每日
- 风控：单只最大 10%，组合 volatility target 15%

**具体步骤**：

| # | 步骤 | 产出 |
|---|------|------|
| 1.1 | 确认 us_bars_1d 在 BQ 中有完整 2020-2026 数据 | 数据就绪 |
| 1.2 | 写 `MomentumStrategy(strategy.Strategy)` | 策略代码 |
| 1.3 | 配置 PaperRunner: us market, $100K 初始 | 配置文件 |
| 1.4 | 跑 2026-01-01 → 至今 | 完整 paper 日志 |
| 1.5 | 验证：订单生成、成交、持仓、风控 | Bug 修复 |

**成功标准**：PaperRunner 无报错跑完，输出每日持仓和 P&L。

**依赖**：us_bars_1d BQ 表就绪（回填完成后自动满足）。

### 3.2 Week 2: 因子库 + ML + Walk-forward

**目标**：双策略 walk-forward 对比，ML 策略用因子库挑因子。

```
BQ us_bars_1d
    │
    ├──→ FactorRegistry ──→ ModelTrainer ──→ MLPredStrategy
    │         │
    │    因子准入: IC>0.02, 覆盖>80%, 无泄漏
    │
    └──→ MomentumStrategy ──────────────────┤
                                              │
                              PaperRunner (双策略并行)
                                              │
                              ExperimentTracker ──→ 对比报告
```

**Walk-forward 设定**：
- 训练窗：2020-2022（3 年滚动）
- 验证窗：2023
- 测试窗：2024-2025
- 2026 至今：Paper 纸交易（out-of-sample）

**评估指标**：

| 指标 | 说明 |
|------|------|
| Rank IC | 预测值与未来收益的 rank 相关性 |
| Sharpe Ratio | 年化（假设无风险利率 2%） |
| Max Drawdown | 最大回撤百分比 |
| Win Rate | 盈利交易日占比 |
| Turnover | 日均换手率 |

**具体步骤**：

| # | 步骤 | 产出 |
|---|------|------|
| 2.1 | 因子库重构完成（独立 spec） | FactorRegistry |
| 2.2 | 写 `MLPredStrategy` | 策略代码 |
| 2.3 | Walk-forward 配置 | 配置文件 |
| 2.4 | ExperimentRunner 跑双策略 | 回测记录 |
| 2.5 | 生成对比报告 | ExperimentTracker 输出 |

**成功标准**：ML 策略的 Rank IC > 0.03，Sharpe > 动量基准 1.2x。

**前置依赖**：因子库重构 spec → 实施（Week 1 期间并行或 Week 2 初期）。

### 3.3 Week 3: 5 分钟频率 + Cron 调度

**目标**：从日线升级到 5m，Paper Runner 改 cron 定时自动跑。

| # | 步骤 | 产出 |
|---|------|------|
| 3.1 | 用 5m 数据重新 walk-forward | 5m 回测结果 |
| 3.2 | 调整策略参数（动量窗口、持仓周期） | 优化后配置 |
| 3.3 | Paper Runner cron 定时（交易日盘中每 5m） | cron job |
| 3.4 | 日志采集 + 错误告警 | systemd journal / 文件 |
| 3.5 | 观察 3+ 交易日稳定运行 | 验证通过 |

**成功标准**：5m Paper 连续 3 个交易日无报错，日内表现与回测偏差 < 20%。

**依赖**：us_bars_5m BQ 表就绪。

### 3.4 Week 4: 评估 & 决策

**目标**：综合回测 + paper 数据，决定是否进入 Live Loop 开发。

**评估矩阵**：

| 指标 | 回测值 | Paper 值 | 衰减 | 容忍上限 | 判定 |
|------|--------|----------|------|----------|------|
| Sharpe | — | — | — | < 20% | |
| MaxDD | — | — | — | < 25% | |
| 胜率 | — | — | — | < 10% | |

**决策树**：

```
衰减可接受？
  ├── ✅ ML > 动量 → 启动 Live Loop 开发
  ├── ✅ ML ≈ 动量 → 优化 ML 因子，再评 1 周
  └── ❌ 衰减严重
        ├── 过拟合？ → 检查训练窗口、因子数量
        ├── 数据泄漏？ → 检查 look-ahead bias
        └── 幸存者偏差？ → 检查标的池历史成分
```

---

## 4. 新增文件清单

| 文件 | 用途 |
|------|------|
| `strategies/momentum.py` | 动量策略 |
| `strategies/ml_pred.py` | LightGBM 预测策略 |
| `strategies/config/momentum_us.yaml` | 动量策略 US 配置 |
| `strategies/config/ml_us.yaml` | ML 策略 US 配置 |
| `scripts/run_paper_momentum.sh` | 动量 paper 启动脚本 |
| `scripts/run_paper_ml.sh` | ML paper 启动脚本 |
| `cron/paper_momentum_5m.sh` | W3 5m cron 脚本 |
| `factors/registry.py` | 因子注册库（独立 spec） |

---

## 5. 风险 & 缓解

| 风险 | 概率 | 缓解 |
|------|------|------|
| 回填数据质量问题 | 中 | 已有 quality check timer |
| PaperRunner 有隐藏 bug | 中 | W1 先用最简单策略暴露 |
| 5m 数据量太大，回测慢 | 中 | 先单线程跑，W3 看是否需要优化 |
| ML 过拟合，paper 衰减大 | 高 | Walk-forward 已在设计中 |
| 因子库重构耗时超预期 | 中 | 独立 spec，不阻塞 W1 |

---

## 6. 待定项

| 项 | 状态 |
|----|------|
| 因子库 spec（独立文档） | 📝 待写 |
| Live Loop 设计 | ⏳ W4 后才启动 |
| 港股/加密币 paper runner | 本 spec 不覆盖，后续扩展 |
