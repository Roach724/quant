# Quant 项目 — 状态备忘

> 更新日期: 2026-05-30 18:00 UTC · 当前分支: `feature/quant-next-phase` · VM: asia-east2-a

---

## 一、整体进度总览

```
数据管道 ██████████████ 100%   ← 回填全完成 + BQ 入库中
因子注册 ██████████████ 100%   ← FactorRegistry BQ双表 + 39因子已入库
Futu 接入 █████████████░  95%   ← OpenD 运行中，ws_collector + cron 1d 已部署
策略验证 ██████████░░░░  70%   ← W1动量验证✅ + W2 ML对比✅，W3/W4待执行
回测引擎 ████████████░  90%   ← ML pred 支持已接入，walk-forward 就绪
OMS/执行 ████████████░  90%   ← Futu Stock/Crypto Broker + Router 已实现
实盘交易 ██████░░░░░░░  40%   ← Paper Runner 就绪，缺 Live Loop + 状态持久化
全自动   ███░░░░░░░░░░  20%   ← 缺实时策略引擎、生产加固
```

---

## 二、策略验证进度（新增）

| 阶段 | 内容 | 状态 | 结果 |
|------|------|------|------|
| W1 | SimpleMomentum 全链路验证 | ✅ | 年化 11.00%, Sharpe 0.76, MaxDD -13.25% |
| W2 | ML LightGBM vs 动量 walk-forward | ✅ | ML 4.00% > 动量 3.32%, Sharpe 0.64 |
| W3 | 5m 频率 + cron 定时 | ⏳ | us_bars_5m 加载中 (122K/456K) |
| W4 | 评估 & Live Loop 决策 | ⏳ | 待 W3 完成 |

### W2 实验详情

```
📊 ML LightGBM vs SimpleMomentum (2026-01 → 2026-05, 20 stocks)

  Metric             Momentum      ML LightGBM
  ───────────────────────────────────────────
  Total Return         3.32%          4.00%  ✅
  Annual Return        8.51%         10.30%  ✅
  Sharpe               0.65           0.64   ≈
  Max Drawdown       -12.34%        -14.90%  ⚠️
  Win Rate            42.00%         43.00%  ✅
```

---

## 三、运行中基础设施

### VM 部署 (GCE asia-east2-a)

| 组件 | 方式 | 状态 | 详情 |
|------|------|------|------|
| OpenD | 手动 | ✅ 运行 | `127.0.0.1:11111`，行情+交易双登录 |
| ws_collector (5m) | systemd | ✅ 运行 | WebSocket 推送，US 234 + HK 15 + Crypto 10 |
| US 1d 采集 | cron (21:30 UTC) | ✅ | Futu API，Mon-Fri，收盘后 |
| HK 1d 采集 | cron (08:30 UTC) | ✅ | Futu API，Mon-Fri，收盘后 |
| BQ Loader ×6 | cron | ✅ | US/HK/Crypto × 1d+5m，Mon-Fri/daily |
| query-api | systemd | ✅ | Go REST API |
| logrotate | cron | ✅ | 30 天轮转 |

### BQ 数据状态

| 表 | 行数 | 状态 |
|----|------|------|
| us_bars_1d | 372,723 | ✅ 就绪 (2020-2026) |
| us_bars_5m | 122,898 | 🔄 fallback 加载中 |
| hk_bars_1d | 1,513 | 🔄 fallback 加载中 |
| hk_bars_5m | 18,950 | 🔄 fallback 加载中 |
| crypto_bars_1d | 10 | ⏸️ 搁置 |
| crypto_bars_5m | 530 | ⏸️ 搁置 |

---

## 四、数据采集架构

```
实时层:   ws_collector (systemd, WebSocket 5m) ──→ GCS ──→ BQ
定时层:   main.py cron ×2 (收盘 1d)            ──→ GCS ──→ BQ
历史层:   backfill.py (回填)                    ──→ GCS ──→ BQ

标的:     三者统一通过 Futu API fetch_supported_symbols() 获取
          US=234 / HK=15 / Crypto=10
GCS 路径: raw/{market}/bars/freq={freq}/year={YYYY}/month={MM}/day={DD}/symbol={SYMBOL}.parquet
BQ 表:    quant.{market}_bars_{freq} — PARTITION BY DATE(timestamp), CLUSTER BY symbol
```

---

## 五、回填进度 — ✅ 全部完成

| # | 任务 | 标的 | 行数 | 状态 |
|---|------|------|------|------|
| 1 | 🇺🇸 US 1d | 234只 | 373,595 | ✅ 完成 + BQ 入库 |
| 2 | 🇭🇰 HK 1d | 15只 | 28,161 | ✅ 完成 + BQ loading |
| 3 | 🇺🇸 US 5m | 234只 | 456,300 | ✅ 完成 + BQ loading |
| 4 | 🇭🇰 HK 5m | 15只 | 23,760 | ✅ 完成 + BQ loading |
| 5 | 🪙 加密币 | 15对 | — | ⏸️ 搁置 |

---

## 六、项目结构（新增模块）

| 目录 | 内容 | 状态 |
|------|------|------|
| `strategies/` | MLPredStrategy + SimpleMomentum | ✅ 新增 |
| `factors/registry.py` | FactorRegistry — BQ 双表 + 准入标准 | ✅ 新增 |
| `factors/evaluation.py` | 因子 IC/衰减/覆盖率评估 | ✅ 新增 |
| `ml/trainer.py` | +load_from_bq() 方法 | ✅ 新增 |
| `sql/factor_registry_schema.sql` | 因子库 BQ 建表 DDL | ✅ 新增 |
| `scripts/init_factor_registry.py` | 初始化 39 因子注册 | ✅ 新增 |
| `scripts/run_paper_momentum.sh` | 动量 Paper Runner 启动 | ✅ 新增 |
| `scripts/run_w2_experiment.py` | W2 双策略对比脚本 | ✅ 新增 |
| `experiment/config_w2.yaml` | W2 walk-forward 配置 | ✅ 新增 |
| `paper/tests/` | PaperRunner 测试 | ✅ 新增 |
| `factors/tests/` | Factor 测试 (37+ tests) | ✅ 新增 |
| `ml/tests/` | ML 测试 | ✅ 新增 |
| `strategies/tests/` | 策略测试 | ✅ 新增 |

---

## 七、最近代码改动 (feature/quant-next-phase)

| Commit | 改动 |
|--------|------|
| `eae0b41` | docs: W3-W4 实施计划 |
| `6cd86b7` | feat: W2 完成 — ML LightGBM beats momentum |
| `8110d39` | fix: FactorBuilder label columns + split_data unpacking |
| `40e2531` | feat: W2 experiment config + run script |
| `91ba154` | feat: MLPredStrategy — LightGBM stock selection |
| `838ed6e` | feat: ModelTrainer.load_from_bq() — BQ+Registry 集成 |
| `e6554b8` | fix: Parquet nanosecond → microsecond + BQ fallback |
| `b9a5706` | docs: W2 ML dual-strategy implementation plan |
| `e40a0fd` | fix: storage.py timestamp microsecond precision |

---

## 八、已解决 vs 待解决

### ✅ 已解决

| 问题 | 解决方案 |
|------|----------|
| Phase 0 数据管道 | VM cron 部署，GCS 路径含 freq 维度 |
| Futu 数据接入 | OpenD + ws_collector + cron 1d + backfill 全链路 |
| Cloud Run 弃用 | 全量迁移到 VM cron |
| BQ dedup 分区冲突 | CREATE OR REPLACE TABLE 显式 PARTITION/CLUSTER |
| GCS 路径 market 错误 | --market → storage_market |
| Parquet 纳秒时间戳 | storage.py 微秒 + BQ loader pandas fallback |
| 因子注册制 | FactorRegistry BQ 双表 + 39 因子已入库 |
| PaperRunner 全链路 | W1 动量验证通过 + W2 ML 双策略跑通 |

### 🔴 仍待解决

| # | 问题 | 说明 |
|---|------|------|
| 1 | **无 Live Trading Loop** | 没有 run_live.py 主循环 |
| 2 | **状态无持久化** | Engine/Portfolio 重启丢失 |
| 3 | **回测引擎单线程** | 大数据量回测慢 |
| 4 | **5m 策略未验证** | W3 待执行 |
| 5 | **未端到端实盘验证** | Paper Runner paper 已跑通，但未持续运行 |

---

## 九、当前决策

| 决策项 | 决定 |
|--------|------|
| 数据源 | Futu API 作为 US/HK/Crypto 主源 |
| 数据查询 | 一律走 BigQuery，GCS 仅写入归档 |
| 部署方式 | VM cron + systemd，不用 Cloud Run |
| 回填策略 | 串行执行（Futu API 限速 60req/30s） |
| 策略验证路线 | B→A：先 paper 验证策略有效性，再上实盘 |
| 因子注册制 | BQ 双表 + IC>0.05/t-stat>3/cov>90% 准入 |
| Parquet 兼容 | storage.py 微秒精度 + BQ loader pandas fallback |
| 加密币 | 暂时搁置 |

---

## 十、教训

| 教训 | 详情 |
|------|------|
| Futu API 限速 | 60/30s，回填必须串行 |
| GCS 路径陷阱 | adapter.market="MIXED" → 写入 raw/mixed/ → 数据丢失 |
| BQ dedup 分区 | CREATE OR REPLACE TABLE 不保留分区→需显式声明 |
| Parquet 纳秒 vs 微秒 | PyArrow 写 INT64_NANOS，BQ 只读 MICROS → 双保险修复 |
| BQ Loader 可并行 | 不依赖 Futu API，4 张表可同时灌 |
| FactorBuilder 缺 label | compute() 必须返回 fwd_ret_5d/20d 供 ML 训练 |
| ModelTrainer split_data | 返回 3 值 (train/val/test)，调方需 unpack 3 个 |
| Backfill 进程卡住 | Futu context 清理阻塞→需手动 kill |
| 文件权限 | /opt/quant 属于 quant:quant，需 sudo |
| at 命令缺失 | 用 OpenClaw cron kind:"at" |

---

## 十一、关键指标

- **代码规模**: Python 15k+ 行 / Go ~2k 行 / Terraform ~600 行
- **测试覆盖**: 140+ Python 测试 + Go 测试
- **因子库**: 39 因子注册 (BQ factor_registry)，准入标准 IC>0.05
- **策略**: SimpleMomentum + MLPredStrategy (LightGBM)
- **BQ 表**: 6 张 + 2 张因子库表，分区+聚簇
- **Git 分支**: `main` / `feature/quant-next-phase` (活跃)
- **VM**: GCE asia-east2-a, Ubuntu 24.04, python3.12
