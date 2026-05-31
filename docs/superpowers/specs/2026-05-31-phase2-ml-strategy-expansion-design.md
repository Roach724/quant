# Phase 2 — ML 升级 + 多策略验证 — 设计文档

日期：2026-05-31 | 状态：设计中 | 前置：Phase 1 已合并

---

## 目标

在 Phase 1（F10 数据采集 + 41 因子 + QARP 策略）的基础上，完成三件事：

1. **W3 5m 策略验证** — 用已有的 365K us_bars_5m 数据跑 5m 频率动量策略
2. **F10 因子 IC 评估** — 验证 41 个 F10 因子中哪些有 alpha，注册到 BQ factor_registry
3. **80 因子 ML 全量回测** — tech(39) + fundamental(41) 合并训练 LightGBM，对比单 tech 基准

同时预留 Phase 3（期权/实时推送）扩展点。

---

## 架构总览

```
┌─ Phase 1 已交付 ────────────────────────────────────────────┐
│  6 个 F10 Adapter → GCS → 12 张 BQ 表                        │
│  TechFactorBuilder (39) + FundamentalFactorBuilder (41)       │
│  factor_values BQ 表（待灌入）                                │
└──────────────────────────────────────────────────────────────┘
                         │
                         ▼
┌─ Phase 2 新增 ───────────────────────────────────────────────┐
│                                                               │
│  [W3] 5m 策略验证                                              │
│    us_bars_5m (365K 行) → PaperRunner 5m config                │
│    SimpleMomentum + MLPredStrategy @ 5m 频率                   │
│                                                               │
│  [IC] 因子 IC 评估                                             │
│    F10 因子从 BQ factor_values / raw F10 表加载               │
│    IC/t-stat/coverage 计算 → FactorRegistry.evaluate()        │
│    通过 admission 的因子 → 注册 + 标记 status=active          │
│                                                               │
│  [ML] 80 因子全量训练                                          │
│    --factor-source all (tech + fundamental)                    │
│    对比 --factor-source tech (baseline)                        │
│    OOS walk-forward → IC/Sharpe/MaxDD 报告                    │
│                                                               │
│  [策略] 新策略                                                 │
│    ShortSqueeze — 高 short_interest + 低 days_to_cover         │
│    SectorRotation — from_plate → 板块轮动                      │
│                                                               │
└──────────────────────────────────────────────────────────────┘
```

---

## 设计决策

| 决策点 | 选择 | 理由 |
|--------|------|------|
| W3 数据源 | BQ `us_bars_5m`（365K 行） | 365K 行足以覆盖 ~6 个月 5m K 线 |
| W3 策略 | SimpleMomentum + MLPredStrategy @ 5m | 复用现有策略，只改频率 |
| F10 因子评估数据源 | BQ F10 raw 表（us_valuation 等） | factor_values 表尚未灌入数据，先用 raw 表直接算 |
| ML 训练数据 | factor_values 表（tech + fundamental） | 先跑一次因子批量计算灌入 factor_values |
| 新策略 | 最多 2 个（ShortSqueeze + SectorRotation） | 聚焦验证，不铺太开 |
| 新 F10 adapter | 暂不新增 | Phase 1 的 6 个先跑稳 |

---

## 文件变更清单

### 新建文件

| 文件 | 说明 |
|------|------|
| `experiment/config_w3_5m.yaml` | W3 实验配置：5m 频率，us_bars_5m BQ 数据源 |
| `scripts/run_w3_experiment.py` | W3 实验启动脚本 |
| `experiment/config_factor_ic.yaml` | F10 因子 IC 评估配置 |
| `scripts/evaluate_f10_factors.py` | 因子 IC 批量评估脚本 |

### 修改文件

| 文件 | 改动 |
|------|------|
| `paper/strategies.py` | 新增 `ShortSqueeze` 策略类 |
| `paper/market.py` | `UniverseBuilder.from_plate` 完善（已实现，测试） |
| `ml/trainer.py` | `load_data_from_bq()` 支持 `--factor-source all` 合并 tech + fundamental |
| `factors/registry.py` | `evaluate()` 支持从 BQ raw table 加载 F10 因子值 |
| `scripts/run_w2_experiment.py` | 扩展为支持 `--factor-source` 参数 |
| `experiment/config_w2.yaml` | 新增 `all` factor source 配置 |

---

## W3 — 5m 策略验证

### 数据

| 维度 | 值 |
|------|-----|
| 数据源 | `quant.us_bars_5m` — 365,470 行，234 只标的 |
| 时间范围 | 2025-12-01 → 2026-05-30（6 个月） |
| 频率 | 5 分钟 K 线 |
| 数据加载 | `engine/data.py` 新增 `BigQuery5mSource` adapter |

### 策略

Two strategies tested:

1. **SimpleMomentum@5m** — 每 5m 计算过去 20 根 K 线动量，选 top 20 买入
2. **MLPredStrategy@5m** — LightGBM 预测未来 1h 收益，每 5m 调仓

### 评估指标

| 指标 | 目标 |
|------|------|
| Sharpe | > 0.5 (5m 频率噪音大，阈值低于日线) |
| Win Rate | > 45% |
| MaxDD | < 20% |
| Turnover/day | < 500% (避免过度交易) |

### 验证步骤

1. BigQuery5mSource 从 BQ 流式加载 5m 数据
2. PaperRunner 用 5m 频率跑 6 个月 walk-forward
3. 对比 1d vs 5m 频率的 Sharpe/MaxDD/Turnover
4. 如 5m 策略跑通 → 标记 W3 done，准备 Live Loop

---

## F10 因子 IC 评估

### 数据流

```
BQ F10 raw tables (us_valuation, us_short_interest, ...)
    ↓ 按 symbol+date JOIN
FundamentalFactorBuilder.compute()
    ↓ 41 因子值
pd.merge with fwd_ret_5d/20d from us_bars_1d
    ↓
IC / t-stat / coverage 计算
    ↓
FactorRegistry.evaluate() → admission
```

### 评估批次

| 批次 | 数据范围 | 符号数 | 预期可用因子 |
|------|---------|--------|------------|
| 估值因子 | 1y PE/PB/PS 趋势 | ~200 | pe/pb/ps_percentile, pe_vs_5y_avg, peg_ratio |
| 空头因子 | 最近 6 个月 | ~200 | short_ratio, days_to_cover, short_volume_pct |
| 分析师因子 | 最新快照 | ~200 | target_price_upside, buy_ratio, rating_mean |
| 财报因子 | 最近 4 季 | ~200 | roe, roa, gross_margin, debt_to_equity |

### 评估标准（与 Phase 1 一致）

| 标准 | 阈值 |
|------|------|
| abs(IC) | > 0.05 |
| abs(t-stat) | > 3.0 |
| coverage | > 90%（日线标的），> 70%（只有部分标的有 F10 数据） |
| min_periods | 30 (日频因子) / 12 (季频因子如 ROE) |

### 目标

≥ **15/41** F10 因子通过 admission，注册到 `factor_registry` 并标记 `status=active`。

---

## ML 80 因子全量训练

### 实验设计

```
Control:  --factor-source tech (39 factors, W2 baseline)
Treatment: --factor-source all (80 factors: 39 + 41)

Configuration:
  训练: 2020-01-01 → 2024-12-31 (walk-forward, 每月 retrain)
  测试: 2025-01-01 → 2026-05-30
  模型: LightGBM (same params as W2)
  标的: top 100 by market cap
  Label: fwd_ret_5d
```

### 数据准备

```
Step 1: Batch compute all 80 factors from BQ
  → TechFactorBuilder: from us_bars_1d BQ
  → FundamentalFactorBuilder: from F10 raw BQ tables
  → Write to factor_values BQ table

Step 2: ML trainer loads from factor_values
  → --factor-source all → LEFT JOIN tech + fundamental by date+symbol
  → Handle NaN from F10 factors (some symbols lack data)
  → Train + backtest
```

### 评估

| 指标 | tech-only (baseline) | tech+fundamental (target) |
|------|---------------------|--------------------------|
| OOS Rank IC | ~0.04 (W2 result) | > 0.05 |
| Sharpe | 0.64 (W2 result) | > 0.70 |
| MaxDD | -14.9% (W2 result) | < -15% |

---

## 新策略

### 1. ShortSqueeze 策略

**逻辑：** 选空头持仓高 + 覆盖天数低 + 近期价格上涨的标的（挤压信号）

```python
class ShortSqueeze(Strategy):
    """Short squeeze: high short interest + low days-to-cover + upward price momentum."""
    
    top_k: int = 10
    rebalance_every: int = 5
    
    # Screen conditions from F10 data:
    min_short_ratio: float = 0.05       # >5% short interest
    max_days_to_cover: float = 5.0       # <5 days to cover
    min_price_momentum_5d: float = 0.02  # >2% recent price increase
```

### 2. SectorRotation 策略

**逻辑：** 每月计算各板块的平均因子值，选因子值最高的板块，买该板块所有股票

```python
class SectorRotation(Strategy):
    """Monthly sector rotation based on factor rankings."""
    
    factor: str = "roe"          # Factor to rank sectors by
    top_k_sectors: int = 3       # Top K sectors to buy
    sectors: list[str] = ["HSI", "HSTECH", ...]  # Plate codes
```

---

## 批处理调度（Phase 2 新增）

```
# Factor batch computation — daily after BQ loader runs
0 7 * * * cd /opt/quant && python3.12 scripts/compute_factors_batch.py --source tech
30 7 * * * cd /opt/quant && python3.12 scripts/compute_factors_batch.py --source fundamental

# Factor IC evaluation — weekly Monday
0 8 * * 1 cd /opt/quant && python3.12 scripts/evaluate_f10_factors.py
```

---

## 为 Phase 3 预留的扩展点

| 扩展点 | 预留方式 |
|--------|---------|
| 期权生态 | `get_option_chain` / `get_option_volatility` / `get_option_expiration_date` Futu API 已验证可用 |
| 内部人交易 | `get_insider_trade_list` API 可用，adapter 模板预留 |
| 经纪商数据 | `get_top_ten_buy_sell_brokers` API 可用 |
| 晨星报告 | `get_research_morningstar_report` API 可用 |
| 公司基本面 | `get_company_profile` / `get_company_executives` / `get_company_operational_efficiency` 可用 |
| 实时推送 | 参考 ws_collector 模式，新增 `ws_f10_collector.py` |
| Live Trading Loop | `run_live.py` — PaperRunner 已验证可行 |

---

## 验证

1. **W3 5m 策略**：SimpleMomentum@5m 跑通，对比 1d 频 Sharpe/MaxDD
2. **F10 因子 IC**：≥15/41 因子通过 admission (|IC|>0.05, |t-stat|>3)
3. **ML 全量**：`--factor-source all` vs `--factor-source tech`，OOS IC 提升 > 0.01
4. **新策略**：ShortSqueeze / SectorRotation @ 最近 1 年 OOS 有正收益
5. **向后兼容**：现有 W1/W2 配置仍可运行
