# Quant 项目 — 状态备忘

> 更新日期: 2026-05-26 · 当前分支: `feature/futu-integration`

---

## 一、整体进度总览

```
数据管道 ████████████░  90%   ← 采集器/BQ/SDK 代码完成，待 Phase 0 重建部署
回测引擎 ████████████░  90%   ← ML pred 支持已接入，walk-forward 就绪
OMS/执行 ████████████░  90%   ← Futu Stock/Crypto Broker + Router 已实现
量化因子 ████████████░  85%   ← FactorBuilder(43+因子) + ModelTrainer + ExperimentTracker 就绪
Futu 接入 ████████████░  85%   ← 全 6 阶段代码完成，待 Phase 0 + OpenD 环境后部署
实盘交易 ██████░░░░░░░  40%   ← Paper Runner 就绪，缺 Live Loop + 状态持久化
全自动   ███░░░░░░░░░░  20%   ← 缺实时数据流、调度部署、生产加固
```

---

## 二、项目结构

### 主项目：`quant/` （活跃开发中）

| 目录 | 内容 | 状态 |
|------|------|------|
| `engine/` | 回测引擎、策略接口、风控规则、walk-forward、ML pred 支持 | ✅ 成熟 |
| `oms/` | OrderManager、Broker 抽象 (Paper/Alpaca/Futu Stock/Futu Crypto)、Router、风控网关 | ✅ 成熟 |
| `execution/` | TWAP/VWAP 算法执行 | ✅ |
| `collectors/` | yfinance/Binance/Futu 数据采集 → GCS | ✅ Futu 适配器已新增 |
| `bigquery_loader/` | 批量加载 Parquet → BigQuery | ✅ |
| `query-api/` | Go REST API 查询 bar 数据 | ✅ |
| `sdk/` | Python SDK (source=direct/api) | ✅ |
| `factors/` | FactorBuilder — 43+ 因子计算引擎 (Alpha158 + HK 特色) | ✅ |
| `ml/` | ModelTrainer — OLS/Ridge/LightGBM + IC 评估 | ✅ |
| `experiment/` | ExperimentTracker + InvestmentRecord + ExperimentRunner | ✅ |
| `paper/` | Paper Runner — 多市场历史数据回放模拟 | ✅ |
| `quality/` | 数据质量监控 (Cloud Run) | ✅ |
| `dashboard/` | FastAPI 看板 | 🟡 基础 |
| `notebooks/` | 策略研究 Notebook | 🟡 进行中 |
| `terraform/` | GCP 基础设施（含 Futu Job 定义，未 apply） | 🟡 代码就绪 |
| `docker/` | Dockerfile.collector + OpenD 启动脚本 | ✅ |
| `docs/` | 设计文档、技术方案、用户手册 | ✅ |

---

## 三、已完成的功能

### 数据层
- ✅ GCS 数据湖：US/HK/Crypto 日线和分钟线
- ✅ BigQuery 分析表：分区+聚簇
- ✅ Go Query API：`GET /api/v1/bars`
- ✅ Python SDK：`quant.data.bars()` 统一接口
- ✅ Futu 数据适配器：`futu_stock_adapter.py` + `crypto_futu_adapter.py`

### 策略与研究层
- ✅ 自定义回测引擎：事件驱动逐根 K 线
- ✅ ML 预测值接入引擎：`DataFrameSource(pred=...)` → `ctx.predictions`
- ✅ 策略基类 `Strategy`：参数自动发现
- ✅ Walk-forward 优化（含 pred 传递）
- ✅ FactorBuilder：43+ 因子（收益率、波动率、动量、成交量、换手率、价格形态、高阶矩、港股特色）
- ✅ ModelTrainer：OLS → Ridge → LightGBM 决策树 + Rank IC 评估
- ✅ ExperimentTracker：实验注册、结果记录、对比报告

### 交易与执行层
- ✅ Broker Protocol：PaperBroker + AlpacaBroker + FutuStockBroker + FutuCryptoBroker
- ✅ RouterOrderManager：符号前缀路由 (HK./US. → stock, CC./BTC/USDT → crypto)
- ✅ TWAP/VWAP 执行算法
- ✅ 风控规则体系：volatility_target、stop_loss、exposure、drawdown
- ✅ OMS：订单生命周期、仓位追踪、引擎对账
- ✅ Paper Runner：`python run_paper.py` 一键纸交易 (US/HK/Crypto)

### 基础设施
- ✅ CI/CD：GitHub Actions + Docker 构建 + Cloud Run 部署
- ✅ Docker：`Dockerfile.collector` + `start_collect.sh` + `FutuOpenD.xml.template`
- ✅ Terraform：Futu Stock/Crypto Collector Job 定义

---

## 四、待解决的问题

### 🔴 P0 — 阻塞部署

| # | 问题 | 说明 | 方案状态 |
|---|------|------|----------|
| 1 | **Phase 0 数据管道修复** | GCS 路径无 freq 维度、WRITE_TRUNCATE 覆写丢失日内数据。Futu 采集器需要 `freq=futu_1m/` 路径才能部署 | 方案已完成，待执行 (需 GCP 权限) |
| 2 | **无实时数据流** | 只有定时批处理，策略引擎无实时推送 | 需接入 Futu WebSocket |
| 3 | **无 Live Trading Loop** | 没有 `run_live.py` 主循环 | 需从 hk-quant 迁移 |
| 4 | **OpenD 运行环境** | 需要一台 24/7 机器 (本地 PC 或 VPS) 运行 OpenD | 需设计部署方案 |
| 5 | **状态无持久化** | Engine/Portfolio/仓位全在内存，重启丢失 | 需设计持久化方案 (Redis/SQLite) |

### 🟡 P1 — 严重影响

| # | 问题 | 说明 |
|---|------|------|
| 6 | **未实际部署** | Terraform 文件已就绪，未 `terraform apply`；需先完成 Phase 0 |
| 7 | **未端到端验证** | 单元测试 mock 了 Futu API，未用真实 OpenD 跑过采集→GCS→BQ→SDK 全链路 |
| 8 | **回测引擎单线程** | 大数据量回测慢；walk-forward 优化受限于单线程 |
| 9 | **无自动状态恢复** | 断网/重启后无法自动恢复仓位与订单状态 |
| 10 | **MEMO.md 与 task_plan.md 可能不同步** | 两份文档需要保持一致 |

### 🟢 P2 — 锦上添花

| # | 问题 | 说明 |
|---|------|------|
| 11 | 无实时监控 Dashboard | 可视化面板待开发 |
| 12 | 本地化/晚盘等特殊逻辑 | HK 午休 12-13 点、半日市等未处理 |
| 13 | 回测报告不丰富 | 缺资金曲线图、归因分析 |
| 14 | 无 CI 覆盖率门禁 | 未强制 code review + coverage |
| 15 | 文档未反映当前状态 | 部分文档日期停留在 5/18-5/20 |

---

## 五、规划路线图

### 短期（5月底 — 6月初）
```
Phase 0: 数据管道修复 ← 当前阻塞
  └─ WRITE_APPEND + freq 分隔 + dedup
  └─ 6 采集器 + 6 BQ loader + 6 BQ 表
  └─ 状态：方案已完成（superpowers/task_plan.md），待执行

Phase 0.5: Futu 接入 Phase 0 后整合
  └─ Futu 采集器以 freq=futu_1m/1d 写入新路径
  └─ 端到端验证：OpenD → 采集 → GCS → BQ → SDK
  └─ 代码 85% 就绪，等 Phase 0 完工即可部署
```

### 中期（6月）
```
Phase 7: Live Trading Loop
  └─ run_live.py 主循环
  └─ 状态持久化 (Redis/SQLite)
  └─ OpenD 部署方案 (本地 PC / VPS)

Phase 8: Crypto 策略上线
  └─ 资金费率套利 Paper Trading → 小资金实盘（优先）
  └─ 截面动量策略验证
  └─ 设计文档已就绪 (docs/research/crypto-strategy-design-2026-05-19.md)
```

### 长期（7月+）
```
Phase 9: 生产加固
  └─ 监控 Dashboard | 告警升级
  └─ 灾难恢复 | 回滚策略
  └─ CI 质量门禁

Phase 10: 多策略组合
  └─ 多策略并行、资金分配
  └─ 策略热加载
  └─ 自动化策略生命周期
```

---

## 六、当前决策/待确认事项

| 决策项 | 选项 | 状态 | 说明 |
|--------|------|------|------|
| 数据管道修复 | Plan B (BQ WRITE_APPEND + dedup) ✅ | 已定案 | 单点改动、GCS 不膨胀 |
| Futu 数据与旧源关系 | 并行新增，不替代 | 已定案 | Futu 以独立 freq 路径写入，yfinance/akshare 保留 |
| 实时数据源 | Futu OpenD | 已定案 | 覆盖 HK+US+Crypto |
| 状态持久化 | Redis vs SQLite | 待定 | 需讨论 |
| 生产部署 | Docker Compose vs systemd | 待定 | 需讨论 |
| OpenD 运行位置 | 本地 PC vs VPS vs Cloud Run 内嵌 | 待定 | Phase 0 后用方案 A (Job 内嵌)，Live Loop 阶段升级方案 B (常驻) |

---

## 七、风险评估

| 风险 | 影响 | 概率 | 缓解措施 |
|------|------|------|----------|
| GCP 费用超预期 | 中 | 中 | 监控 Cloud Run 用量，设置预算告警 |
| Futu API 断连 | 高 | 低 | 多 broker 冗余 (Binance 备选)；旧源采集器保留不删 |
| 数据延迟/缺失 | 高 | 中 | 数据源回退机制、Quality 监控告警 |
| 策略失效/过拟合 | 高 | 中 | Walk-forward + 实盘监控 + 手动熔断 |
| 美股 API 行情卡未购买 | 中 | 中 | 美股 fallback 到 yfinance；确认 Nasdaq Basic 行情卡状态 |
| 历史 K 线额度 300 (30 天滚动) | 低 | 低 | 优先高优 symbol，重复回填不消耗额度 |

---

## 八、关键指标

- **代码规模**: Python 12k+ 行 / Go ~2k 行 / Terraform ~600 行
- **测试覆盖**: 104+ 个 Python 测试 + Go 测试
- **基础设施**: GCP (Cloud Run Jobs/Services, GCS, BigQuery, Cloud Scheduler)
- **数据市场**: US (YFinance+Futu) / HK (YFinance+akshare+Futu) / Crypto (Binance+Futu)
- **Broker 适配器**: PaperBroker / AlpacaBroker / FutuStockBroker / FutuCryptoBroker / CryptoBinanceBroker
- **Git 分支**: `main` (Phase 1 生产) / `feature/futu-integration` (活跃开发)
- **Futu 集成阶段**: P0-P6 代码完成，等待 Phase 0 数据管道修复后部署
