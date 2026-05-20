# Quant 项目 — 状态备忘

> 更新日期: 2026-05-19 · 协调员: Jarvis 🤖

---

## 一、整体进度总览

```
数据管道 ██████████░░░  70%   ← Phase 1 done，待升级 append-only
回测引擎 ████████████░  85%   ← Phase 2 done，walk-forward 就绪
OMS/执行 ████████████░  85%   ← Phase 3+4 done，TWAP/VWAP/风控就绪
量化因子 █████████░░░░  60%   ← 因子构建 + ML 训练器就绪，策略研究进行中
港股实盘 ██████░░░░░░░  35%   ← hk-quant 有 live_trader/paper_trader，未投产
全自动   ███░░░░░░░░░░  15%   ← 缺实时数据流、调度、Futu 接入
```

---

## 二、项目结构

### 主项目：`quant/` （活跃开发中）

| 目录 | 内容 | 状态 |
|------|------|------|
| `engine/` | 回测引擎、策略接口、风控规则、walk-forward优化、组合管理 | ✅ 36 tests, 成熟 |
| `oms/` | 订单管理、Broker抽象(Paper/Alpaca/Crypto)、预盘风控网关、桥接层 | ✅ 32 tests, 成熟 |
| `execution/` | TWAP/VWAP 算法执行 | ✅ |
| `collectors/` | yfinance/Binance 数据采集 → GCS (Parquet+JSON) | ✅ 8 tests |
| `bigquery_loader/` | 批量加载 Parquet → BigQuery | ✅ |
| `query-api/` | Go REST API 查询 bar 数据 | ✅ |
| `sdk/` | Python SDK (source=direct/api) | ✅ 3 tests |
| `factors/` | 因子计算引擎 | ✅ 基础功能 |
| `ml/` | XGBoost/LightGBM 训练器 | ✅ 基础功能 |
| `experiment/` | 实验管理 + 投资记录追踪 | ✅ |
| `quality/` | 数据质量监控 (Cloud Run) | ✅ |
| `dashboard/` | FastAPI 看板 | 🟡 基础 |
| `notebooks/` | 策略研究 Notebook x 3 | 🟡 进行中 |
| `terraform/` | GCP 基础设施代码 | ✅ |
| `superpowers/` | 开发记录/计划 | — |
| `docs/` | 设计文档、技术方案、用户手册 | ✅ |

### 归档项目：`hk-quant/`（已冻结，参考用）

| 组件 | 说明 |
|------|------|
| `live_trader.py` | 实盘交易引擎（含信号生成→风控→网关→PnL追踪） |
| `paper_trader.py` | 纸交易运行器 |
| `trade_gateway.py` | 网关抽象（paper/IB） |
| `risk_manager.py` | 独立风控模块 |
| `config/live_trading.yaml` | 交易参数配置（$1M HKD 资金、20只持仓、15%回撤熔断等） |
| `data/experiments/` | 多组实验记录 |

---

## 三、已完成的功能（工程化底座）

### 数据层
- ✅ GCS 数据湖：US/HK/Crypto 日线和分钟线 Parquet 原始数据
- ✅ BigQuery 分析表：3 张表分区+聚簇
- ✅ Go Query API：`GET /api/v1/bars?market=us&symbols=AAPL` 实时查询
- ✅ Python SDK：`quant.data.bars()` 统一接口（direct GCS / API 双通道）
- ✅ CI/CD：GitHub Actions + Docker 构建 + Cloud Run 部署

### 策略与研究层
- ✅ 自定义回测引擎：事件驱动逐根K线遍历
- ✅ 策略基类 `Strategy`：`on_init` / `on_bar` 模式，参数自动发现
- ✅ Walk-forward 优化
- ✅ 因子构建管线（corp actions、fundamentals 合并）
- ✅ ML 模型训练（LightGBM/XGBoost）
- ✅ 实验管理 + 数据源回退机制

### 实盘骨架
- ✅ 风控规则体系：volatility_target、stop_loss、exposure、drawdown 组合
- ✅ OMS：订单生命周期管理、仓位追踪、与引擎对账
- ✅ Broker Protocol：PaperBroker（确定性模拟）、AlpacaBroker（真实 API）
- ✅ TWAP/VWAP 执行算法
- ✅ 预盘风控网关 RiskGateway
- ✅ 告警系统
- ✅ 数据质量监控质量服务

---

## 四、待解决的问题

### 🔴 P0 — 阻塞全自动交易

| # | 问题 | 说明 | 方案状态 |
|---|------|------|----------|
| 1 | **无实时数据流** | 目前只有定时批处理（5min/1d），策略引擎无实时推送 | 需接入 Futu WebSocket |
| 2 | **无 Live Trading Loop** | 没有 `run_live.py` 主循环：「取数据→跑策略→风控→下单→循环」 | 需从 hk-quant 的 live_trader 迁移 |
| 3 | **常驻调度/进程管理** | 无 systemd/Docker daemon/cron 使系统 24/7 运行 | 需设计部署方案 |
| 4 | **Futu 券商未接入** | `docs/plans/` 有设计文档，OMS Broker 层未实现 Futu client | 设计就绪，需编码 |
| 5 | **状态无持久化** | Engine/Portfolio/仓位全在内存，重启即丢失 | 需设计持久化方案 |

### 🟡 P1 — 严重影响

| # | 问题 | 说明 |
|---|------|------|
| 6 | **数据管道覆写丢失** | GCS 覆写 + lookback 120min → 日内日内分钟线丢失；已出方案（WRITE_APPEND + dedup + 频率分隔），**等待执行** |
| 7 | **HK 日线/分钟线路径冲突** | 同一 GCS 路径被日线和分钟线采集器相互覆写；与 #6 一并解决 |
| 8 | **US 缺少日线采集器** | 只有 1m（yfinance），无 1d 管线 |
| 9 | **回测引擎单线程** | 大数据量回测慢；walk-forward 优化受限于单线程 |
| 10 | **无自动状态恢复** | 断网/重启后无法自动恢复仓位与订单状态 |

### 🟢 P2 — 锦上添花

| # | 问题 | 说明 |
|---|------|------|
| 11 | 无实时监控 Dashboard | 只有 alerting 通知，无可视化面板 |
| 12 | 本地化/晚盘等特殊逻辑 | HK 午休12-13点、半日市等未处理 |
| 13 | 数据源仅 YFinance + Binance | 容灾不足，HK 数据依赖 YFinance 可能有延迟 |
| 14 | 回测报告不丰富 | 基础 metric 有，但缺资金曲线图、归因分析 |
| 15 | 无 CI 覆盖率门禁 | 未强制 code review + coverage |

---

## 五、规划路线图

### 短期（5月第4周 — 6月初）
```
Phase 0: 数据管道修复
  └─ WRITE_APPEND + freq 分隔 + dedup（6采集器×6加载器×6表）
  └─ 销毁现有 → 重建
  └─ 状态：方案已完成（superpowers/），待执行

Phase 6: 港股实盘就绪（从 hk-quant 迁移）
  └─ 迁移 live_trader → quant/
  └─ 统一使用 quant/ 目录的 engine + oms + risk
  └─ 纸交易验证
```

### 中期（6月）
```
Phase 7: Futu 接入
  └─ 实现 Broker Protocol for Futu OpenD
  └─ 支持港股/US/美股正股 + 期权
  └─ 实时行情 WebSocket → 策略引擎

Phase 8: Live Trading Loop
  └─ run_live.py 主循环
  └─ 状态持久化（Redis / SQLite）
  └─ 系统服务化（Docker compose / systemd）
```

### 长期（6-7月）
```
Phase 9: 生产加固
  └─ 监控 Dashboard | 告警升级
  └─ 灾难恢复 | 回滚策略
  └─ CI 质量门禁 | 代码审查

Phase 10: 多策略组合
  └─ 多策略并行运行、资金分配
  └─ 策略热加载
  └─ 自动化策略生命周期管理
```

---

## 六、当前决策/待确认事项

| 决策项 | 选项 | 推荐 | 说明 |
|--------|------|------|------|
| 数据管道修复方案 | Plan B (BQ WRITE_APPEND + dedup) ✅ 已选 | 单点改动、无需 GCS 文件膨胀 |
| 实时数据源 | Futu OpenD vs Alpaca WebSocket vs Binance WS | Futu OpenD | Futu 覆盖 HK+US+期权，比 YFinance 可靠 |
| 状态持久化 | Redis vs SQLite | Redis | 速度快、支持 pub/sub 通知 |
| 生产部署 | Docker Compose vs systemd vs K8s | Docker Compose | 轻量、与现有 GCP 架构一致 |

---

## 七、风险评估

| 风险 | 影响 | 概率 | 缓解措施 |
|------|------|------|----------|
| GCP 费用超预期 | 中 | 中 | 监控 Cloud Run 用量，设置预算告警 |
| Futu API 断连 | 高 | 低 | 多 broker 冗余（Alpaca 备选） |
| 数据延迟/缺失 | 高 | 中 | 数据源回退机制、Quality 监控告警 |
| 策略失效/过拟合 | 高 | 中 | Walk-forward + 实盘监控 + 手动熔断开关 |

---

## 八、关键指标

- **代码规模**: Python 10k+ 行 / Go ~2k 行 / Terraform ~500 行
- **测试覆盖**: 104 个 Python 测试 + Go 测试
- **基础设施**: GCP (Cloud Run Jobs/Services, GCS, BigQuery, Cloud Scheduler)
- **数据市场**: US (YFinance) / HK (YFinance) / Crypto (Binance)
- **Git 分支**: `main` (Phase 1 生产) / `phase3-execution-oms` (活跃开发)
