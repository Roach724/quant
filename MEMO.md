# Quant 项目 — 状态备忘

> 更新日期: 2026-05-30 · 当前分支: `feature/futu-integration` · VM: asia-east2-a

---

## 一、整体进度总览

```
数据管道 █████████████░  95%   ← 采集器/BQ/GCS 已部署运行，回填进行中
Futu 接入 █████████████░  95%   ← OpenD 运行中，ws_collector + cron 1d 已部署
回测引擎 ████████████░  90%   ← ML pred 支持已接入，walk-forward 就绪
OMS/执行 ████████████░  90%   ← Futu Stock/Crypto Broker + Router 已实现
量化因子 ████████████░  85%   ← FactorBuilder(43+) + ModelTrainer + ExperimentTracker
实盘交易 ██████░░░░░░░  40%   ← Paper Runner 就绪，缺 Live Loop + 状态持久化
全自动   ███░░░░░░░░░░  20%   ← 缺实时策略引擎、生产加固
```

---

## 二、运行中基础设施

### VM 部署 (GCE asia-east2-a)

| 组件 | 方式 | 状态 | 详情 |
|------|------|------|------|
| OpenD | 手动 | ✅ 运行 | `127.0.0.1:11111`，行情+交易双登录 |
| ws_collector (5m) | systemd | ✅ 运行 | WebSocket 推送，US 234 + HK 15 + Crypto 10 |
| US 1d 采集 | cron (21:30 UTC) | ✅ | Futu API，Mon-Fri，收盘后 |
| HK 1d 采集 | cron (08:30 UTC) | ✅ | Futu API，Mon-Fri，收盘后 |
| BQ Loader ×4 | cron | ✅ | US/HK × 1d+5m，Mon-Fri |
| BQ Loader ×2 | cron | ✅ | Crypto × 1d+5m，daily |
| query-api | systemd | ✅ | Go REST API |
| logrotate | cron | ✅ | 30 天轮转 |

### GCP 云服务

| 组件 | 状态 |
|------|------|
| GCS 数据桶 (`deductive-notch-495015-c2-quant-data`) | ✅ |
| BigQuery `quant` dataset (6 张分区分聚簇表) | ✅ |
| Cloud Run | ❌ 已弃用，迁移到 VM |

---

## 三、数据采集架构

```
实时层:   ws_collector (systemd, WebSocket 5m) ──→ GCS ──→ BQ
定时层:   main.py cron ×2 (收盘 1d)            ──→ GCS ──→ BQ
历史层:   backfill.py (回填)                    ──→ GCS ──→ BQ

标的:     三者统一通过 Futu API fetch_supported_symbols() 获取
          US=234 / HK=15 / Crypto=10
GCS 路径: raw/{market}/bars/freq={freq}/year={YYYY}/month={MM}/day={DD}/symbol={SYMBOL}.parquet
BQ 表:    quant.{market}_bars_{freq} — PARTITION BY DATE(timestamp), CLUSTER BY symbol
```

### GCS 路径示例
```
raw/us/bars/freq=1d/year=2020/month=01/day=02/symbol=US.AAPL.parquet
raw/hk/bars/freq=5m/year=2026/month=05/day=26/symbol=00700.parquet
```

---

## 四、回填进度 (2026-05-30)

| # | 任务 | 标的 | 状态 | GCS 文件 | ETA |
|---|------|------|------|----------|-----|
| 1 | 🇺🇸 US 1d | 234只 | 🔄 4/7 chunk | 116K+ parquet | ~13:30 UTC |
| 2 | 🇭🇰 HK 1d | 15只 | ⏳ at-job 16:00 | — | ~16:30 UTC |
| 3 | 🇺🇸 US 5m | 234只 | ⏳ at-job chain | — | ~16:45 UTC |
| 4 | 🇭🇰 HK 5m | 15只 | ⏳ at-job chain | — | ~16:50 UTC |
| 5 | 🪙 加密币 | 15对 | ⏸️ 搁置 | — | — |

> 串联脚本: `/opt/quant/scripts/backfill_chain.sh`  
> at-job: `de771f47` (16:00 UTC) — HK 1d → US 5m → HK 5m → BQ Loader  
> 追踪: `/opt/quant/docs/backfill-tracker.md`

---

## 五、项目结构

| 目录 | 内容 | 状态 |
|------|------|------|
| `engine/` | 回测引擎、策略接口、风控、walk-forward、ML pred | ✅ |
| `oms/` | OrderManager、Broker (Paper/Alpaca/Futu Stock/Crypto)、Router、风控 | ✅ |
| `execution/` | TWAP/VWAP 算法执行 | ✅ |
| `collectors/` | backfill.py / main.py / ws_collector.py + adapters (Futu/YFinance/Binance) | ✅ |
| `bigquery_loader/` | GCS Parquet → BigQuery 批量加载 + 去重 | ✅ |
| `query-api/` | Go REST API 查询 bar 数据 | ✅ |
| `sdk/` | Python SDK (source=direct/api) | ✅ |
| `factors/` | FactorBuilder — 43+ 因子 (Alpha158 + HK 特色) | ✅ |
| `ml/` | ModelTrainer — OLS/Ridge/LightGBM + IC 评估 | ✅ |
| `experiment/` | ExperimentTracker + InvestmentRecord | ✅ |
| `paper/` | Paper Runner — 多市场历史回放模拟 | ✅ |
| `quality/` | 数据质量监控 | ✅ |
| `scripts/` | cron_wrapper.sh / backfill_chain.sh | ✅ |
| `docker/` | Dockerfile.collector + OpenD 启动脚本 | ✅ |
| `terraform/` | GCP 基础设施 IaC | ✅ |
| `docs/` | 设计文档、回填追踪 | ✅ |

---

## 六、最近代码改动 (5 commits)

| Commit | 日期 | 改动 |
|--------|------|------|
| `ec89bdb` | 5/30 | docs: 回填追踪表更新 |
| `603fa1e` | 5/30 | feat: 串联回填脚本 (HK 1d → US 5m → HK 5m → BQ) |
| `a1d8c84` | 5/30 | fix: BQ loader dedup 保留分区/聚簇 |
| `4a762db` | 5/30 | fix: backfill --market 做 storage_market |
| `0c6fa1a` | 5/29 | feat: MARKET env / 动态标的 / 市场过滤 |

---

## 七、已解决 vs 待解决

### ✅ 已解决 (原 P0 阻塞项)

| 问题 | 解决方案 |
|------|----------|
| Phase 0 数据管道 | VM cron 部署，GCS 路径含 freq 维度，WRITE_APPEND + dedup |
| Futu 数据接入 | OpenD 运行中，ws_collector + cron 1d + backfill 全链路 |
| WebSocket 实时数据 | ws_collector systemd 服务，订阅 259 标的 5m K 线 |
| Cloud Run 弃用 | 全量迁移到 VM cron |
| BQ dedup 分区冲突 | CREATE OR REPLACE TABLE 显式声明 PARTITION BY + CLUSTER BY |
| GCS 路径 market 错误 | --market 参数传递到 storage_market |

### 🔴 仍待解决

| # | 问题 | 说明 |
|---|------|------|
| 1 | **无 Live Trading Loop** | 没有 run_live.py 主循环，策略不能自动执行 |
| 2 | **状态无持久化** | Engine/Portfolio/仓位在内存，重启丢失 |
| 3 | **回测引擎单线程** | 大数据量回测慢 |
| 4 | **加密币模块待部署** | crypto/ 包已开发但搁置 |
| 5 | **未端到端实盘验证** | Paper Runner 已就绪但未持续运行 |

---

## 八、当前决策

| 决策项 | 决定 |
|--------|------|
| 数据源 | Futu API 作为 US/HK/Crypto 主源 |
| 数据查询 | 一律走 BigQuery，GCS 仅写入归档 |
| 部署方式 | VM cron + systemd，不用 Cloud Run |
| 回填策略 | 串行执行（Futu API 限速 60req/30s） |
| 加密币 | 已开发，暂时搁置 |
| 旧采集器 | yfinance/akshare 代码保留但不启用 |

---

## 九、教训 (新增)

| 教训 | 详情 |
|------|------|
| Futu API 限速 | 60 请求/30秒，US backfill 单进程 ~1.67 req/s，并行超限 |
| GCS 路径陷阱 | adapter.market="MIXED" 导致写入 raw/mixed/，需显式传 --market |
| HK 数据丢失 | 路径正确前 HK 回填 23K 行丢失，需重跑 |
| BQ dedup | CREATE OR REPLACE TABLE AS SELECT 不保留分区属性 |
| 文件权限 | /opt/quant 属于 quant:quant，DangXuan 需 sudo |
| at 命令缺失 | 系统无 at，用 OpenClaw cron kind:"at" |
| 串行回填 | 60/30s 限制决定不能并行跑多个 backfill |

---

## 十、关键指标

- **代码规模**: Python 13k+ 行 / Go ~2k 行 / Terraform ~600 行
- **测试覆盖**: 104+ Python 测试 + Go 测试
- **数据市场**: US (Futu 234只) / HK (Futu 15只) / Crypto (Binance 10对)
- **Broker 适配器**: Paper / Alpaca / FutuStock / FutuCrypto / Binance
- **BQ 表**: 6 张 (us/hk/crypto × 1d/5m)，按时戳分区 + 按 symbol 聚簇
- **Git 分支**: `main` / `feature/futu-integration` (活跃)
- **VM**: GCE asia-east2-a, Ubuntu 24.04, python3.12
