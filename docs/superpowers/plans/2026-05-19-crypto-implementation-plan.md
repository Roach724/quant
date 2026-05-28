# Crypto 量化策略实现计划

**计划日期**: 2026-05-19（已修正）
**依据设计**: `docs/research/crypto-strategy-design-2026-05-19.md`（量化专家 📈 输出）
**实现团队**: 数据工程师 🔧 / ML建模师 🧠 / 数据分析师 📊 / 工程开发 🚀
**目标**: 从 Paper Trading 验证一路到小资金实盘

---

## ✅ 数据基建现状（纠正后）

### 已在稳定运行的

| 组件 | 市场 | 频率 | 数据范围 | 状态 |
|------|:----:|:----:|:---------:|:----:|
| US 5m 采集 → GCS → BQ | us | 5m | 2026-05-18 ~ 今 | ✅ 运行中 |
| US 1d 采集 → GCS → BQ | us | 1d | 2026-05-18 ~ 今 | ✅ 运行中 |
| HK 5m 采集 → GCS → BQ | hk | 5m | 2026-05-18 ~ 今 | ✅ 运行中 |
| HK 1d 采集 → GCS → BQ | hk | 1d | 2026-05-18 ~ 今 | ✅ 运行中 |
| **Crypto 5m** → GCS → BQ | crypto | 5m | 2026-05-18 ~ 今 | ✅ 运行中 |
| **Crypto 1d** → GCS → BQ | crypto | 1d | 2026-05-18 ~ 今 | ✅ 运行中 |

### 三个市场全部已就绪

- **6 个 Cloud Run Jobs** 定时运行，3 个市场 × 2 种频率
- **6 张 BQ 表** 持续加载（`us_bars_5m / us_bars_1d / hk_bars_5m / hk_bars_1d / crypto_bars_5m / crypto_bars_1d`）
- **SDK** `quant.data.bars()` 可直接读取以上任意市场
- **Paper Runner** `run_paper.py --market crypto` 已支持 24/7 交易时段
- **收集器代码** `adapters/crypto_binance_adapter.py` 已完成并部署

### 仍然缺少的

| 缺失项 | 影响 | 解决 |
|--------|:----:|------|
| **历史数据**（仅 2026-05-18 起） | 🔴 回测区间不够（才 1 天） | 跑 `backfill.py --source cryptobinance` 回填 2020~2026 |
| **资金费率数据** | 🔴 资金费率套利策略无数据 | 新增 `collectors/crypto_funding_rate.py` |
| **OI（未平仓量）数据** | 🟡 OI 因子缺数据 | 新增 `collectors/crypto_open_interest.py` |
| 实时 WebSocket | 🟡 实盘需要 | Phase 4 |

---

## 路线图总览

```
            6月第1周              6月第2周              6月第3周              6月第4周
          ┌─────────────────┐   ┌─────────────────┐   ┌─────────────────┐   ┌─────────────────┐
          │ Phase 1:         │   │ Phase 2:         │   │ Phase 3:         │   │ Phase 4:         │
          │ 数据补齐 + 回填    │   │ 资金费率套利策略   │   │ 截面动量策略      │   │ 综合策略 + 实盘   │
          ├─────────────────┤   ├─────────────────┤   ├─────────────────┤   ├─────────────────┤
 Prior:   │ • 回填 2020~今    │   │ • 策略实现        │   │ • 策略实现        │   │ • 链上数据接入    │
 New:     │ • 费率采集器       │   │ • Paper Trading  │   │ • Paper Trading  │   │ • 多策略融合      │
          │ • OI 采集器       │   │ • 回测报告        │   │ • 回测报告        │   │ • $5K 实盘验证    │
 Wait:    │ • 本地缓存模块     │   │ • Walk-forward   │   │ • 组合优化        │   │ • 监控上线        │
          └─────────────────┘   └─────────────────┘   └─────────────────┘   └─────────────────┘
```

---

## Phase 1: 数据补齐 + 回填（6月第1周）

### 目标
补齐 Crypto 策略所需的历史数据和衍生品数据（资金费率、OI），让 Paper Runner 能加载完整回测区间。

### 任务清单

| # | 任务 | 负责 | 产出 | 预估 | 依赖 |
|---|------|------|------|:----:|:----:|
| 1.1 | **Crypto 历史数据回填**：`backfill.py --source cryptobinance --all --frequency 1d --start 2020-01-01` 跑一次回填 | 🚀 工程开发 | GCS + BQ 覆盖 2020~今，1d 数据 | 1 天 | — |
| 1.2 | **Crypto 5m 分批回填**：2023-01~今的 5m 数据（避免 API 限频，每周回填 1 个季度，批量跑几次） | 🚀 工程开发 | GCS + BQ 覆盖 2023~今，5m 数据 | 2 天 | — |
| 1.3 | **资金费率采集器**：基于 CCXT `fetch_funding_rate_history()`，每 8h 采集全币种，存入 GCS `raw/crypto/funding_rate/` | 🔧 数据工程师 | `collectors/crypto_funding_rate.py`，部署为 Cloud Run Job | 1 天 | — |
| 1.4 | **OI 采集器**：基于 CCXT `fetch_open_interest_history()`，每日一次 | 🔧 数据工程师 | `collectors/crypto_open_interest.py`，部署为 Cloud Run Job | 0.5 天 | — |
| 1.5 | **本地数据缓存模块**：`paper/crypto_loader.py`，通过 SDK `quant.data.bars()` 加载 GCS/BQ 数据为 `DataFrameSource` | 🔧 数据工程师 | Paper Runner 可直接 `--data-source sdk` 加载 Crypto 数据 | 0.5 天 | 1.1, 1.2 |
| 1.6 | **CoinGecko 市值排名快照**：每月 Top 50 币种列表，用于动态币种池（避免幸存者偏差） | 🔧 数据工程师 | `collectors/coin_rank_history.py` + 本地缓存 | 1 天 | — |

### 数据流（Paper Runner 中）
```
PaperRunner
  └── crypto_loader.py
        ├── quant.data.bars(market="crypto", frequency="1d", ...)  ← GCS/BQ 源
        ├── → pd.DataFrame (close/open/high/low/volume)
        └── → DataFrameSource
              └── → engine.Engine.run(strategy, data)
```

### 验收标准
- [ ] `python run_paper.py --market crypto --data-source sdk --start 2023-01-01 --end 2026-05-19 --capital 50000 --strategy BuyHold` 正常运行
- [ ] 脚本跑通且能看到 bar 数据被遍历
- [ ] 资金费率采集器部署并写入 GCS
- [ ] 回填后 BQ `crypto_bars_1d` 表覆盖 2020-01 ~ 今

---

## Phase 2–4

（保持不变，内容与之前一致，仅去掉 CCXT 采集器实现相关任务 — 因为已有）

### Phase 2: 资金费率套利策略（6月第2周）

| # | 任务 | 负责 | 产出 | 预估 |
|---|------|------|------|:----:|
| 2.1 | **资金费率套利策略**：继承 `Strategy` 基类 | 🧠 ML建模师 | `paper/strategies/funding_rate_reversal.py` | 1 天 |
| 2.2 | **衍生品因子计算**：资金费率 z-score、OI 变化、组合信号 | 🧠 ML建模师 | `paper/factors/crypto_derivatives.py` | 1 天 |
| 2.3 | **Crypto 成本模型**：计入资金费率收支、Taker/Maker、滑点 | 🧠 ML建模师 | `paper/crypto_cost.py` | 0.5 天 |
| 2.4 | **回测跑批**：2023-01~今（5m 数据区间）全区间 + 多参数网格 | 📊 数据分析师 | 回测报告 + 参数敏感性 | 1.5 天 |
| 2.5 | **Walk-forward 验证**：6 个月滚动窗口，验证参数稳定性 | 📊 数据分析师 | Walk-forward 报告 | 1 天 |
| 2.6 | **Paper Trading 连续运行**：7 天无人值守 | 🚀 工程开发 | 日志 + 日报 | 2 天 |

### Phase 3: 截面动量策略（6月第3周）

| # | 任务 | 负责 | 产出 | 预估 |
|---|------|------|------|:----:|
| 3.1 | **截面动量策略**实现 | 🧠 ML建模师 | `paper/strategies/cross_sectional_momentum.py` | 1 天 |
| 3.2 | **动量因子计算**：多周期动量、量价背离、波动率过滤 | 🧠 ML建模师 | `paper/factors/crypto_momentum.py` | 1.5 天 |
| 3.3 | **动态币种池**：按市值+成交量过滤，每月更新 | 🧠 ML建模师 | `paper/crypto_universe.py` | 0.5 天 |
| 3.4 | **回测跑批**：2023-01~今 + 参数网格 + walk-forward | 📊 数据分析师 | 回测报告 | 1.5 天 |
| 3.5 | **组合策略验证**：费率套利 + 动量协方差分析 | 📊 数据分析师 | 组合优化报告 | 1 天 |
| 3.6 | **Paper Trading**：7 天 | 🚀 工程开发 | 日志 + 日报 | 2 天 |

### Phase 4: 综合策略 + 实盘（6月第4周）

| # | 任务 | 负责 | 产出 | 预估 |
|---|------|------|------|:----:|
| 4.1 | **链上数据接入**：Glassnode Free Tier | 🔧 数据工程师 | `collectors/onchain_glassnode.py` | 1 天 |
| 4.2 | **链上因子计算**：活跃地址变化、MVRV、NVT | 🧠 ML建模师 | `paper/factors/onchain.py` | 1 天 |
| 4.3 | **综合多因子模型**：资金费率 40% + 动量 30% + 链上 20% + 情绪 10% | 🧠 ML建模师 | `paper/strategies/multi_factor.py` | 1 天 |
| 4.4 | **BTC 宏观轮动**：BTC.D + DXY + ETF Flow 做仓位管理 | 🧠 ML建模师 | `paper/strategies/btc_timing.py` | 0.5 天 |
| 4.5 | **全策略组合回测** | 📊 数据分析师 | 组合绩效报告 | 1.5 天 |
| 4.6 | **Paper Trading 终验**：14 天连续运行 | 🚀 工程开发 | 终验报告 | 3 天 |
| 4.7 | **$5K 实盘**：Binance API 小规模运行 | 🚀 工程开发 | 实盘记录 | 2 天 |

---

## 文件结构规划

```
quant/
├── paper/
│   ├── strategies/
│   │   ├── funding_rate_reversal.py       # Phase 2
│   │   ├── cross_sectional_momentum.py     # Phase 3
│   │   ├── multi_factor.py                # Phase 4
│   │   └── btc_timing.py                  # Phase 4
│   ├── factors/
│   │   ├── crypto_derivatives.py           # Phase 2
│   │   ├── crypto_momentum.py             # Phase 3
│   │   └── onchain.py                     # Phase 4
│   ├── crypto_loader.py                   # Phase 1
│   ├── crypto_universe.py                 # Phase 3
│   └── crypto_cost.py                     # Phase 2
├── collectors/
│   ├── crypto_funding_rate.py             # Phase 1 — 新增
│   ├── crypto_open_interest.py            # Phase 1 — 新增
│   ├── onchain_glassnode.py               # Phase 4
│   └── coin_rank_history.py               # Phase 1
│   ├── crypto_binance_adapter.py          # ✅ 已有
│   ├── backfill.py                        # ✅ 已有（支持 crypto）
│   └── main.py                            # ✅ 已有（支持 cryptobinance）
├── data/crypto/                           # 本地缓存
└── docs/research/
    ├── crypto-strategy-design-2026-05-19.md   # 设计文档
    └── crypto-implementation-plan-2026-05-19.md # 本文件
```

---

## 实现建议

**Phase 1 可以立即开始**：需要确认 GCP 账号的 gcloud 和 docker 权限，跑 `backfill.py` 回填 + 部署新采集器。

需要启动 Phase 1 吗？
