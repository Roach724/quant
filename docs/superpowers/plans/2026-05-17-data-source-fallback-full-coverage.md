# Data Source Fallback + Full Symbol Coverage

> **Owner:** Derick | **日期:** 2026-05-17 | **状态:** 待执行

## 背景

当前量化项目（quant）存在两个核心问题：

1. **标的覆盖不全** — US 和 HK 均为硬编码小列表（US: 53 只, HK: 37 只），无法覆盖市场全貌
2. **单点数据源风险** — US 和 HK 均只依赖 yfinance，接口不稳定时当天数据空洞，无自动降级

hk-quant（旧港股项目）已验证的方案：yfinance primary + akshare fallback + 串行限速，覆盖 561 只港股通标的。

## 目标

- **三个市场全部**采用动态全股票池（而非硬编码小列表）
- **US + HK** 回填和线上采集均引入双源 fallback 机制
- **Crypto** 不动（Binance ccxt 已稳定）

## 现状 vs 目标

| 维度 | 现状 | 目标 |
|------|------|------|
| **US 标的数** | 53 只硬编码 S&P 500 | ~500 只（全 S&P 500） |
| **HK 标的数** | 37 只硬编码 | ~500+ 只（全港股通） |
| **CRYPTO 标的数** | 20 只硬编码 | ✅ 不变 |
| **US 回填源** | yfinance 单源 | yfinance → akshare stock_us_hist fallback |
| **HK 回填源** | yfinance 单源 | yfinance → akshare stock_hk_hist fallback |
| **US 线上源** | yfinance 单源 | yfinance → akshare fallback（日线） |
| **HK 线上源** | yfinance 单源 | yfinance → akshare fallback（日线） |
| **US/HK 分钟级** | yfinance 30 天限制 | ⚠️ 维持现状，fallback 仅限日线 |

## 数据源评估

### akshare 适用性

| 功能 | 接口 | 已验证 |
|------|------|--------|
| 港股通成分股列表 | `stock_hk_ggt_components_em()` | ✅ hk-quant 已生产使用 |
| 港股日线 | `stock_hk_hist(symbol, period="daily")` | ✅ hk-quant 覆盖 561 只 |
| 美股日线 | `stock_us_hist(symbol="MSFT")` | ⚠️ 接口存在，需验证格式 |
| 美股 S&P 500 列表 | `stock_us_spot_em()` | 存在，可从海外调用 |
| 连接问题 | 海外调用可能超时 | fallback 放在外层调用，非阻塞主流程 |

### yfinance 适用性

| 维度 | 情况 |
|------|------|
| 日线 — 通用 | ✅ 无时间限制 |
| 分钟线 — 通用 | ⚠️ 免费版仅限最近 30 天 |
| 分钟线 — large batch | ❌ 易触发 rate limit |
| 股价 < 1 HKD 仙股 | ❌ 质量低，自动过滤 |
| 冷门股 | ⚠️ yfinance 可能无数据 → fallback 至 akshare |

## 方案详述

### 一、股票池方案（全市场动态拉取）

#### HK 股票池

复用 hk-quant 已验证的方式：

```
akshare stock_hk_ggt_components_em()  →  港股通全部成分股
    ↓
价格过滤(≥1 HKD) + 流动性过滤(日均成交额≥100万HKD)
    ↓
最终池 ~500+ 只
```

**动态获取**：每次运行时拉取最新列表（港股通成分股每月更新，但变化不大）

**替代方案**：也可缓存一份到 `config/stock_pool_hk.csv`，定时刷新。

#### US 股票池

```
方式一：akshare stock_us_spot_em()  → 全部美股（~8000+，太多）
方式二：抓取 S&P 500 成分股列表（wiki / 静态维护）
方式三：本地维护 config/stock_pool_us.csv
```

**推荐**：方式二（S&P 500 ~500 只，覆盖美股大部分流动性和可交易性）或方式三（手动维护白名单）。

#### CRYPTO 股票池

维持现有 20 只主流交易对，或从 Binance 获取 USDT 交易对列表（~200+，可按交易量过滤 top N）。

### 二、回填方案（backfill.py 改造）

```
for each symbol in [全股票池]:
    1. yfinance 请求
    2. ✅ 成功且行数 >= 5 → 保存，继续
    3. ❌ 失败/空/行数 < 5 → akshare fallback 请求
    4. ✅ fallback 成功 → 保存
    5. ❌ 全部失败 → 日志记录，跳过
    6. sleep(delay) 控制频率
```

**参数调整**：

| 参数 | 当前值 | 新值 |
|------|--------|------|
| 请求间隔 `sleep` | 3s（统一） | 0.3~1s（串行，自动调节） |
| 股票池来源 | 手动 CLI 参数 | 默认全池 |
| progress 日志 | 每 chunk | 每 50 只 |
| 失败后等待 | 加倍 sleep | 加倍 sleep + 最多重试 2 次 |

### 三、线上采集方案（main.py / Cloud Run Jobs 改造）

#### 容器内实现

```
collectors/main.py（定时采集）：

1. 启动时动态获取全股票池（不硬编码）
2. yfinance.fetch_bars(symbols, ...)
3. 如果返回空或非常少 → akshare fallback（日线场景）
4. 写入 GCS
```

#### 分钟级 vs 日线

| 频率 | 数据源 | 股票池 |
|------|--------|--------|
| 5m 分钟级 | yfinance 单源（仅最近 2h） | 前 N 只（控制 GCS 写入量） |
| 1d 日线 | yfinance + akshare fallback | 全股票池 |

**分钟级维持 yfinance 单源**的原因：akshare 的分钟接口格式不同（`stock_hk_hist_min_em`），且分钟级回填需至少 `limit=1000` 翻页，不适合线上实时。

#### Cloud Run Job 配置

| Job | 频率 | 股票池 | Fallback |
|-----|------|--------|---------|
| `collector_us_5m` | 5m | 前 20 只 | ❌ 无 |
| `collector_us_1d` | 1d | 全 S&P 500 | ✅ akshare |
| `collector_hk_5m` | 5m | 前 20 只 | ❌ 无 |
| `collector_hk_1d` | 1d | 全港股通 | ✅ akshare |
| `collector_crypto_5m` | 5m | 全池 | N/A |
| `collector_crypto_1d` | 1d | 全池 | N/A |

### 四、架构设计

```
                    ┌──────────────────┐
                    │   Collector Job  │
                    │  (main.py)       │
                    └────────┬─────────┘
                             │
                    ┌────────▼─────────┐
                    │  MarketAdapter   │
                    │  (Protocol)      │
                    └────────┬─────────┘
                             │
              ┌──────────────┼──────────────┐
              │              │              │
     ┌────────▼───┐  ┌──────▼──────┐  ┌───▼────────┐
     │ yfinance   │  │ akshare     │  │ Binance    │
     │ Adapter    │  │ Adapter(new)│  │ ccxt       │
     └────────────┘  └─────────────┘  └────────────┘
```

**新增 akshare Adapter**：独立 Adapter 类，供 US/HK 的 fallback 路径使用。

## 工作量估算

### HK 部分（优先）

| 任务 | 文件 | 预估 |
|------|------|------|
| 1.1 创建 `AkshareHKAdapter` | `adapters/akshare_hk_adapter.py` | ⏱ 1h |
| 1.2 `YFinanceHKAdapter` 集成 fallback | `adapters/yfinance_hk_adapter.py` | ⏱ 0.5h |
| 1.3 动态股票池（港股通） | `stock_pool.py`（从 hk-quant 移植） | ⏱ 0.5h |
| 1.4 `backfill.py` HK 改造 | `backfill.py` | ⏱ 0.5h |
| 1.5 `main.py` HK 改造 | `main.py` | ⏱ 0.5h |
| 1.6 Terraform HK 配置更新 | `cloud_run_hk.tf` | ⏱ 0.5h |
| **小计** | | **⏱ 3.5h** |

### US 部分

| 任务 | 文件 | 预估 |
|------|------|------|
| 2.1 创建 `AkshareUSAdapter` | `adapters/akshare_us_adapter.py` | ⏱ 1h |
| 2.2 `YFinanceUSAdapter` 集成 fallback | `adapters/yfinance_adapter.py` | ⏱ 0.5h |
| 2.3 动态股票池（S&P 500） | `stock_pool.py` 扩展 | ⏱ 0.5h |
| 2.4 `backfill.py` US 改造 | `backfill.py` | ⏱ 0.5h |
| 2.5 `main.py` US 改造 | `main.py` | ⏱ 0.5h |
| 2.6 Terraform US 配置更新 | `cloud_run_jobs.tf` | ⏱ 0.5h |
| **小计** | | **⏱ 3.5h** |

**总计**: ~7h

### 不做的（明确排除）

- ❌ US/HK 分钟级大规模回填（换数据源超出 scope）
- ❌ Crypto 任何改动（已稳定）
- ❌ akshare 在分钟级线上采集的应用
- ❌ GCS 路径重构（这是另一个 Phase 的话题）

## 回滚方案

如果 akshare fallback 导致线上采集变慢或不稳定：
1. 回滚 `main.py` 到单 yfinance 版本（去掉 fallback fallback 调用）
2. 还原 Terraform 的 SYMBOLS 环境变量
3. 保留 akshare Adapter 代码，后续可单独调试
