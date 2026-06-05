# 量化交易系统 — 规范与子系统手册

> 最后更新: 2026-06-04 | 维护者: Jarvis + 老大
>
> 本文档汇总项目中所有已建立的规范、子系统、约定和使用方式。
> 新成员或新功能开发前应先阅读本文档。

---

## 目录

1. [项目架构总览](#1-项目架构总览)
2. [CI/CD 规范](#2-cicd-规范)
3. [日志规范](#3-日志规范)
4. [标的配置规范 (SSOT)](#4-标的配置规范-ssot)
5. [数据采集子系统](#5-数据采集子系统)
6. [因子注册与计算规范](#6-因子注册与计算规范)
7. [模型注册与训练规范](#7-模型注册与训练规范)
8. [实验管理规范](#8-实验管理规范)
9. [Live Runner 子系统](#9-live-runner-子系统)
10. [Paper Run 子系统](#10-paper-run-子系统)
11. [BQ 直写规范](#11-bq-直写规范)
12. [Dashboard 子系统](#12-dashboard-子系统)
13. [市场日历规范](#13-市场日历规范)
14. [市场时段保护](#14-市场时段保护)
15. [生产禁区 (quant-prod)](#15-生产禁区-quant-prod)

---

## 1. 项目架构总览

```
/opt/quant-prod/
├── collectors/       # 数据采集 (ws_collector, F10, 因子采集器)
├── common/           # 共享模块 (bq_writer, logging_util)
├── config/           # 全局配置 (symbols.yaml SSOT)
├── dashboard/        # FastAPI :8090 + Vue 3 SPA
├── engine/           # 交易引擎 (Portfolio, Strategy, DataSource)
├── factors/          # 因子系统 (TechFactorBuilder, FactorRegistry)
├── live/             # 实时模拟子系统 (LiveRunner, ExperimentManager)
├── ml/               # 模型训练与注册 (MLflow, ModelRegistry)
├── paper_run/        # 纸交回测子系统
├── scripts/          # 运维脚本 (cron, deploy, backfill, train)
├── strategies/       # 策略实现 (MLPredStrategy, SimpleMomentum)
├── systemd/          # 系统服务定义
├── tests/            # 测试
└── docs/             # 文档与 spec
```

**环境分离：**
- **主工作目录**: `/opt/quant-dev/` — **所有开发必须在此完成**（代码开发、测试、PR）
- **生产**: `/opt/quant-prod/` — 只做数据采集、入库、实盘交易
- **流程**: dev → git commit → PR → CI → merge stable → CD → prod
- **禁止在 prod 直接开发**, 禁止在 prod 执行训练/回测/因子计算等开发操作
- **无论多紧急，不准跳过 dev 直接改 prod**

**关键路径：**
- 数据流: Futu OpenD → ws_collector → BQ → LiveRunner → Dashboard
- 模型流: BQ factor_values → train_*.py → MLflow → ModelRegistry → LiveRunner
- 部署流: GitHub → CI (ruff+mypy+pytest) → CD (deploy.sh) → systemd restart

---

## 2. CI/CD 规范

### 2.1 CI (GitHub Actions)

**触发**: `main` 分支的 push 和 PR

**流水线** (`.github/workflows/ci.yml`):

| Job | 工具 | 检查内容 |
|-----|------|---------|
| `python-lint` | ruff | 代码风格 (`ruff check`) + 格式化 (`ruff format --check`) |
| `python-typecheck` | mypy | 类型检查 (`mypy collectors/ --ignore-missing-imports`) |
| `python-test` | pytest + cov | 单元测试 + 覆盖率 |
| `terraform-check` | terraform | fmt + validate |

**作用域**: `collectors/` + `quality/` 目录

### 2.2 CD (GitHub Actions)

**触发**: `stable` 分支的 push

**流程** (`.github/workflows/deploy.yml`):
1. `deploy.sh fetch` — 拉取最新代码到 `/opt/quant-prod/`
2. `pip install -r requirements.txt` — 安装依赖
3. Smoke test — 验证核心模块可导入
4. 重启 ws-collector systemd 服务（**有市场保护**，见 §14）

### 2.3 使用方式

```bash
# 开发者流程
git checkout -b feature/xxx
# ... 开发 ...
git add -A && git commit -m "feat: xxx"
git push origin feature/xxx
# → 创建 PR 到 main → CI 自动运行
# → PR 合并到 main → 合并到 stable → CD 自动部署
```

**分支保护**: `stable` 和 `main` 都禁止直接 push，必须通过 PR。

---

## 3. 日志规范

### 3.1 统一日志模块

**位置**: `common/logging_util.py`

**职责**: 提供 `QuantJsonFormatter` 和 `_ContextFilter`，所有组件统一输出结构化 JSON 日志。

**日志格式**:
```json
{
  "ts": "2026-06-04T12:00:00.000Z",
  "level": "INFO",
  "logger": "ws_collector",
  "quant_env": "prod",
  "quant_module": "collector",
  "msg": "Flushed 270 bars (market=HK) → BQ"
}
```

### 3.2 日志目录

```
/var/log/quant/
├── prod/              # 生产环境日志
│   ├── collector/     # ws_collector 日志
│   ├── live/          # LiveRunner 日志 (按 exp_id 命名)
│   ├── factor/        # 因子采集器日志
│   ├── cron/          # Cron 任务日志
│   └── train/         # 模型训练日志
└── dev/               # 开发环境日志
```

### 3.3 使用方式

```python
# 组件内导入标准 logging
import logging
logger = logging.getLogger("my_module")

# 在入口文件中配置 JSON handler
from common.logging_util import QuantJsonFormatter, _ContextFilter
fh = logging.FileHandler("/var/log/quant/prod/my_module/app.log")
fh.setFormatter(QuantJsonFormatter())
logging.getLogger().addHandler(fh)

# 日志自动带上 quant_env ("prod"/"dev") 和 quant_module
```

**生产环境**: 日志由 Ops Agent 采集到 GCP Logs Explorer，可在 Cloud Console 查看和搜索。

---

## 4. 标的配置规范 (SSOT)

### 4.1 位置

**文件**: `config/symbols.yaml`

**角色**: 全项目唯一真实来源（SSOT）。所有组件（ws_collector, backfill, 因子采集器, LiveRunner）统一引用。

### 4.2 结构

```yaml
markets:
  us:
    symbols:
      - US.AAPL
      - US.MSFT
      # ... 234 只美股
  hk:
    symbols:
      - HK.00001
      - HK.00005
      # ... 270 只港股
  crypto:
    symbols:
      - CRYPTO.BTC
      # ... 10 只加密货币
```

### 4.3 修改规则

- **只增不删标识** — 要移除某标的，将其注释掉不要删行
- **变更走 PR → CI → CD** 流程
- **符号格式**: `{MARKET}.{CODE}` — 美股 5 字符（`US.AAPL`），港股 5 位补零（`HK.00005`），加密货币无补零
- **禁止自设标的数量** — 必须以 `config/symbols.yaml` 为准（US=234, HK=270, Crypto=10）

### 4.4 使用方式

```python
import yaml
with open("config/symbols.yaml") as f:
    cfg = yaml.safe_load(f)
us_symbols = cfg["markets"]["us"]["symbols"]
```

---

## 5. 数据采集子系统

### 5.1 WebSocket 实时 K 线采集

**组件**: `collectors/ws_collector.py` (systemd: `ws-collector.service`)

**职责**: 通过 Futu OpenD WebSocket 订阅 US/HK 实时 5 分钟 K 线，写入 BQ。

**运行方式**:
```bash
# systemd (生产)
sudo systemctl status ws-collector
sudo systemctl restart ws-collector   # 手动重启（市场时段需批准）

# 环境变量
OPEND_HOST=127.0.0.1 OPEND_PORT=11111
GCS_BUCKET=deductive-notch-495015-c2-quant-data
FLUSH_INTERVAL_SEC=300    # 每 5 分钟 flush 到 BQ
BUFFER_MAX=500            # 缓冲区上限
HEARTBEAT_INTERVAL_SEC=1800  # 心跳间隔 30 分钟
```

**架构**:
```
Futu OpenD → BarHandler (去重) → drain_to_buffer → flush → BQ (hk_bars_5m / us_bars_5m)
                                ↑
                          订阅轮转 (market_hours + preheat 5min)
                          看门狗 (>1h 无心跳 → 强制重连)
```

**关键设计**:
- **BarHandler**: CurKlineHandlerBase 推送同一 bar 多次（OHLC 展开），Handler 保留最新版本去重
- **订阅轮转**: 根据 market_calendar 判断交易时段，开盘前 5 分钟预热订阅，午休/收盘自动退订
- **看门狗**: 主循环超过 1 小时无心跳 → 强制断开 OpenD 重连
- **市场保护**: 部署脚本在开盘期间跳过 ws_collector 重启（见 §14）

### 5.2 数据回填

**脚本**: `collectors/backfill.py`

```bash
# 每日回填 (通过 cron)
python collectors/backfill.py --market us --start 2020-01-01 --end 2026-06-03

# 港股回填 1d bars
python scripts/hk_backfill_1d.py
```

数据来源: Futu API (历史 K 线), yfinance (US fallback), akshare (HK fallback)

### 5.3 日线聚合

**脚本**: `scripts/compute_daily_bars.py`

从 5m K 线聚合为日线，写入 BQ `us_bars_1d` / `hk_bars_1d`。

### 5.4 F10 数据采集

**组件**: `collectors/collect_futu_factors.py` (通过 cron_wrapper.sh)

采集 Futu 基本面数据:
- `us_rating_summary` — 美股评级汇总
- `us_insider_trade` — 美股内部人交易
- `us_capital_distribution` — 美股股本分布
- Morningstar 报告
- 分红、财报、高管信息等

### 5.5 关键约束

- **时间戳**: Futu 返回当地时间，ws_collector **存的是当地时间（未转 UTC）**。Dashboard 读取时做修正
- **BQ 写入**: 通过 `common/bq_writer.py` → `insert_rows_json`（30s 超时）
- **GCS 备份**: `scripts/backup_bq_to_gcs.sh` (每日 06:00 UTC)

---

## 6. 因子注册与计算规范

### 6.1 FactorRegistry

**位置**: `factors/registry.py`

**职责**: 在 BQ 中维护因子元数据注册表，管理因子生命周期。

**BQ 表**:
- `quant.factor_registry` — 因子注册信息
- `quant.factor_evaluations` — 因子评估结果

### 6.2 关键方法

```python
from factors.registry import FactorRegistry

reg = FactorRegistry()

# 注册新因子
reg.register(
    factor_id="us_ret_5d",
    name="5-Day Return",
    category="momentum",
    source="tech",
    description="5-day price momentum",
)

# 获取活跃因子列表
active = reg.get_active(market="us")  # → DataFrame

# 停用因子
reg.deactivate("us_ret_5d", reason="low IC")

# 因子评估
reg.evaluate(factor_id, values, fwd_ret_1d, fwd_ret_5d)
# → 写入 IC_mean, IC_std, IC_tstat, coverage 等指标
```

### 6.3 TechFactorBuilder

**位置**: `factors/tech_builder.py`

**职责**: 从 OHLCV 数据计算 39 个技术因子。

**因子列表** (39 个):
- 动量类: ret_1d, ret_5d, ret_10d, ret_20d, ret_60d, ret_120d, streak
- 波动类: vol_5d, vol_10d, vol_20d, vol_60d, vol_ratio_5d, vol_ratio_20d, vol_trend, low_vol_proxy
- 形态类: bb_width, bb_position, macd, macd_signal, macd_hist, rsi_14
- 分布类: skew_20d, skew_60d, skew_120d, kurt_20d, kurt_60d, kurt_120d
- 量价类: avg_turnover_5d, avg_turnover_20d, turnover_ratio, turnover_growth
- 价格结构: daily_range, gap, upper_shadow_ratio, lower_shadow_ratio, price_position_20d, price_stability
- 相关性: corr_vp_20d, vp_divergence

### 6.4 因子批量计算

**脚本**: `scripts/compute_factors_batch.py`

```bash
# 全量计算
python scripts/compute_factors_batch.py --source tech --market hk \
  --start 2020-01-01 --end 2026-06-03

# 增量（最近 30 天）
python scripts/compute_factors_batch.py --source tech --market us --incremental

# 基本面因子
python scripts/compute_factors_batch.py --source fundamental --market us
```

**写入**: BQ `quant.factor_values` 表 (`factor_id, symbol, date, value, source_builder, computed_at`)

### 6.5 因子命名规范

格式: `{market}_{factor_name}` — 如 `us_ret_5d`, `hk_rsi_14`

在 BQ factor_values 中存储，训练时自动去掉 market 前缀作为特征名。

---

## 7. 模型注册与训练规范

### 7.1 ModelRegistry

**位置**: `ml/registry.py`

**后端**: MLflow (SQLite: `$HOME/.mlflow/mlflow.db`, Artifacts: `/opt/quant-prod/models_artifacts/`)

**关键方法**:
```python
from ml.registry import ModelRegistry

# 保存模型
version = ModelRegistry.save(
    name="us_tech",
    model=trained_lgb_model,
    config={"model": {"type": "lightgbm"}, "factors": {...}, "data": {...}},
    metrics={"rmse": 0.1565, "ic": 0.045},
    features=feature_column_names,
    dataset_name="us_tech_v2",
)
# → 自动注册到 MLflow + promote 到 Production stage

# 加载模型
bundle = ModelRegistry.load("us_tech", version=2)  # or "latest"
# bundle.model, bundle.features, bundle.config, bundle.metrics

# 查看版本
versions = ModelRegistry.list_versions("us_tech")
```

### 7.2 模型版本规范

- **版本号**: MLflow 自动递增，删除后无法复用（全局自增）
- **命名**: `{market}_{source}` — 如 `us_tech`, `hk_tech`
- **Stage**: 保存时自动 promote 到 `production`
- **Metadata**: 每次保存记录 git_commit, n_features, label, dataset

### 7.3 训练脚本

```bash
# 美股 tech 模型
python scripts/train_us_tech_v1_explicit.py
# → 20 轮 Optuna 调参 → LightGBM → 注册到 MLflow

# 港股 tech 模型 (从 factor_values 直读)
python scripts/train_hk_tech_v1.py
# → BQ factor_values wide pivot → LightGBM → 注册

# 通用训练器
python ml/trainer.py --market us --source tech
```

### 7.4 模型配置规范

训练脚本应记录以下 config:
```yaml
model:
  type: lightgbm
  name: us_tech
factors:
  source: tech
  mode: explicit | bq_factor_values
  n_factors: 39
data:
  label: fwd_ret_5d
  market: us
  date_range: 2020-01-01_2025-12-31
training:
  n_trials: 20
  n_train: 123456
  n_val: 30864
```

---

## 8. 实验管理规范

### 8.1 实验 ID 格式

```
{type}_{market}_{strategy}_v{version}

type:     live  (实时模拟)  |  paper (纸交回测)  |  prod (实盘)
market:   us | hk | crypto
strategy: ml  (MLPred)  |  mom (Momentum)
version:  数字，从 1 递增

示例:
  live_us_ml_v2      — 美股 ML 策略实时模拟 v2
  live_hk_mom_v2     — 港股动量实时模拟 v2
  paper_us_ml_test   — 美股 ML 纸交测试
```

### 8.2 ExperimentManager

**位置**: `live/experiment_manager.py`

**注册表**: `/var/quant/experiments/registry.json`

**生命周期状态机**:
```
pending → running → paused → running (resume)
                  → completed → archived
                  → failed
```

**关键方法**:
```python
from live.experiment_manager import ExperimentManager
mgr = ExperimentManager()

# 注册
mgr.register("live", "us", "ml", 2, "live/configs/exp1.yaml", name="...")

# 生命周期
run_id = mgr.start("live_us_ml_v2")     # → run_id
mgr.pause("live_us_ml_v2")              # → 保存状态
new_run = mgr.resume("live_us_ml_v2")   # → 新 run_id，base_run 指向旧 run
mgr.stop("live_us_ml_v2")              # → completed
mgr.archive("live_us_ml_v2")           # → archived

# 查看
mgr.list(_type="live", status="running")
mgr.get("live_us_ml_v2")
mgr.runs("live_us_ml_v2")
```

### 8.3 CLI

```bash
python -m live.exp_cli register live/us/ml/2 --config live/configs/exp1.yaml
python -m live.exp_cli list [--type live] [--status running]
python -m live.exp_cli start|pause|resume|stop|archive <exp_id>
python -m live.exp_cli show <exp_id>
python -m live.exp_cli runs <exp_id>
```

### 8.4 Run 隔离

- 每次 `start` / `resume` 自动生成唯一 `run_id`（`YYYYMMDD_HHMMSS`）
- BQ 表 `experiment_equity` / `experiment_trades` 支持按 `run_id` 筛选
- Dashboard 默认展示最新 run，可切换历史 run
- 不同 run 数据完全隔离，不会掺杂

### 8.5 保护机制

| 操作 | 前置条件 | 拒绝 |
|------|---------|------|
| start | pending/paused/completed/archived | "Cannot start: running" |
| pause | running | "Cannot pause" |
| resume | paused | "Cannot resume" |
| stop | running/paused | "Cannot stop" |
| archive | completed/failed | "Cannot archive" |

### 8.6 实验配置 yaml

```yaml
experiment:
  type: live          # live | paper | prod
  market: us
  strategy: ml
  version: 2
  name: "MLPredStrategy us_tech v2"
```

ID 由 `{type}_{market}_{strategy}_v{version}` 自动生成，不在 yaml 中硬编码。

### 8.7 Dashboard 隔离

- **Live Tab**: 仅显示 `live_*` 实验
- **Paper Run Tab**: 仅显示 `paper_*` 实验
- **Prod Tab**: 仅显示 `prod_*` 实验（只读）
- 不同类型实验互相不可见，杜绝串扰

---

### 8.8 Debug 实验（开发调试专用）

**目的**：代码改动先走 debug 实验验证，确认无误后再应用到正式实验。

**配置**：`live/configs/debug_us_ml.yaml`（type=debug，数据完全隔离）

**启动**：
```bash
cd /opt/quant-prod
.venv/bin/python3 live/run.py --config live/configs/debug_us_ml.yaml
```

**Dashboard**：Debug Tab 自动过滤 `debug_*` 实验，不会和 Live/Prod/Paper 混在一起。

**约束**：
- 验证代码改动 → 用 debug config 启动
- 观察结果确认 OK → 再应用到正式实验
- 禁止直接在正式实验上 debug（每次重启都会产生新 run_id + BQ 垃圾数据）

## 9. Live Runner 子系统

### 9.1 概述

**位置**: `live/runner.py` + `live/run.py`

**职责**: 实时模拟交易引擎。三种模式: paper（回放历史）, live（实时）, multi-day（跨天持久化）。

### 9.2 启动方式

```bash
# 通过实验管理 CLI
python -m live.exp_cli start live_us_ml_v2

# 或直接运行
python live/run.py --config live/configs/exp1_ml_us.yaml
```

### 9.3 运行模式

| 模式 | 配置 | 数据源 | 用途 |
|------|------|--------|------|
| paper | `mode: paper` | DataFrameSource (BQ 历史) | 单次回测 |
| live | `mode: live` | BQDataSource (实时 5m) | 单日实时 |
| multi-day | `mode: live` + `multi_day: true` | BQDataSource | 多日跨天，状态持久化 |

### 9.4 核心组件

| 组件 | 文件 | 职责 |
|------|------|------|
| LiveRunner | `live/runner.py` | 主循环、调仓、风控 |
| BQDataSource | `live/bq_datasource.py` | 从 BQ 实时拉取 5m bar |
| StateManager | `live/state.py` | 持仓/资金/风控 JSON 持久化 |
| DashboardObserver | `dashboard/observer.py` | 逐 bar 写 equity/trades 到 BQ |
| Reporter | `live/reporter.py` | 生成 HTML 回测报告 |

### 9.5 关键指标

**写入 BQ 的字段**:
```python
# experiment_equity
ts, exp_id, run_id, bar, equity, cash, portfolio_value, daily_pnl, drawdown

# experiment_trades
ts, exp_id, run_id, bar, symbol, side, qty, price, commission
```

**指标含义**:
- `equity` = cash + sum(持仓 qty × 当前价)
- `daily_pnl` = 当天 equity - 当天开盘 equity
- `drawdown` = (历史峰值 equity - 当前 equity) / 峰值 equity
- `bar` = 5 分钟 bar 序号（从每天开盘开始计数）

### 9.6 配置示例

```yaml
live:
  mode: live
  market: us
  output_dir: output/live/

broker:
  live:
    type: paper
    initial_capital: 100000
    slippage_bps: 5
    commission_bps: 1

strategy:
  name: MLPredStrategy
  model_name: us_tech
  model_version: 2
  top_k: 5
  rebalance_every: 13

risk:
  max_drawdown: 0.15
  max_daily_loss: 0.05

state:
  enabled: true
  dir: /var/quant/state/ml/
  checkpoint_interval: 300
```

---

## 10. Paper Run 子系统

### 10.1 概述

**位置**: `paper_run/`

**职责**: 历史数据回测，计算完整绩效指标，结果写入 BQ 供 Dashboard 展示。

### 10.2 启动方式

```bash
# CLI
python -m paper_run --config live/configs/paper_us.yaml

# 验证配置
python -m paper_run --config live/configs/paper_us.yaml --dry-run

# 环境变量
PAPER_RUN_CONFIG=live/configs/paper_us.yaml python -m paper_run
```

### 10.3 数据流

```
LiveRunner (mode=paper) → equity_curve.csv → compute_all_metrics()
                              ↓
                    DashboardObserver → BQ experiment_equity / experiment_trades
                              ↓
                    PaperRunRunner → BQ paper_runs / paper_metrics
```

### 10.4 绩效指标

**计算位置**: `paper_run/metrics.py`

| 指标 | 含义 |
|------|------|
| sharpe | 年化夏普比率 |
| sortino | 年化索提诺比率（下行风险） |
| max_drawdown | 最大回撤 |
| calmar | 年化收益 / 最大回撤 |
| cagr | 复合年化增长率 |
| annual_return | 年化收益率 |
| annual_vol | 年化波动率 |
| win_rate | 胜率 |
| profit_factor | 总盈利 / 总亏损 |
| total_return | 总收益率 |

### 10.5 实验配置

最小配置 (`paper_simple.yaml`):
```yaml
live: {mode: paper, market: us, output_dir: output/live/}
broker: {paper: {initial_capital: 100000, slippage_bps: 5, commission_bps: 1}}
strategy: {name: SimpleMomentum, lookback: 20, top_k: 5}
experiment: {type: paper, market: us, strategy: mom, version: 1}
```

---

## 11. BQ 直写规范

### 11.1 组件

**位置**: `common/bq_writer.py`

**职责**: 统一 BQ 写入接口，替代旧的 GCS → BQ Loader 流程。

### 11.2 API

```python
from common.bq_writer import write_bars_to_bq, write_rows_to_bq

# 写入 bar 数据
write_bars_to_bq(df, table_id="hk_bars_5m")

# 写入通用数据
write_rows_to_bq(df, table_name="factor_values")
```

### 11.3 设计要点

- **使用 `insert_rows_json`**: 简单可靠，适合当前数据量（~1 bar/s）
- **30 秒超时**: 包在 `ThreadPoolExecutor(timeout=30)` 中，防止 BQ 卡死主线程
- **重试机制**: 最多 3 次，指数退避 (1s, 2s, 4s)，每次重建 BQ client
- **异常处理**: ServiceUnavailable / ResourceExhausted 自动重试
- **GCS 备份**: 每日 06:00 UTC 通过 `backup_bq_to_gcs.sh` 归档

### 11.4 注意事项

- **Streaming Buffer**: `insert_rows_json` 写入的数据有 ~90 分钟的 streaming buffer 延迟，此期间无法 DELETE/UPDATE
- **Infinity 值**: 写入前必须过滤 `np.isinf()` 值，否则 BQ 拒绝
- **不要用 Storage Write API**: 当前数据量不需要

---

## 12. Dashboard 子系统

### 12.1 架构

```
DashboardObserver → BQ (experiment_equity, experiment_trades, paper_runs, paper_metrics)
     ↓
FastAPI server (:8090) → REST API
     ↓
Vue 3 SPA (index.html) → Chart.js + Plotly
     ↓
cloudflared tunnel → 公网访问
```

### 12.2 关键技术

- **前端**: 单文件 SPA，Vue 3 CDN，无构建步骤
- **图表**: Plotly.js (K 线) + Chart.js
- **实时推送**: WebSocket (`/ws/live`)
- **API**: FastAPI + BigQuery Python client

### 12.3 API 端点

| 端点 | 用途 |
|------|------|
| `GET /api/experiments?type=live` | 实验最新快照（按类型过滤） |
| `GET /api/experiments/meta` | 实验元数据（含休眠实验） |
| `GET /api/equity/{exp_id}?run_id=X` | 权益曲线（按 run 过滤） |
| `GET /api/trades/{exp_id}?run_id=X` | 交易记录 |
| `GET /api/experiments/{exp_id}/positions` | 当前持仓（自动补 HK 前导零） |
| `GET /api/experiments/{exp_id}/runs` | 历史 run 列表 |
| `GET /api/paper-runs` | Paper run 列表 |
| `GET /api/paper-runs/{run_id}` | 单次 paper run 详情 |
| `GET /api/market/{market}/{symbol}` | K 线图数据（时区修正+去重） |
| `GET /api/pipeline` | 数据管道健康状态 |

### 12.4 时区修正

BQ 中 bar 的时间戳是 Futu 当地时间（错标 UTC）。Dashboard API 读取时自动修正:
- **HK**: `TIMESTAMP_SUB(timestamp, INTERVAL 8 HOUR)`
- **US**: `TIMESTAMP(DATETIME(timestamp), "America/New_York")` (自动处理夏令时)

### 12.5 启动方式

```bash
cd /opt/quant-prod && .venv/bin/python3 -m uvicorn dashboard.server:app --host 0.0.0.0 --port 8090
# 访问: http://localhost:8090
```

---

## 13. 市场日历规范

### 13.1 组件

**位置**: `live/market_calendar.py`

**注意**: 文件名为 `market_calendar.py`（不是 `calendar.py`）——避免与 Python stdlib `calendar` 模块冲突。

### 13.2 关键方法

```python
from live.market_calendar import MarketCalendar

cal = MarketCalendar("hk")  # "us" | "hk" | "crypto"

cal.is_open_now(preheat_minutes=5)  # 当前是否交易时段（含开盘前预热）
cal.is_trading_day()                # 今天是否是交易日
cal.time_until_open()               # 距离开盘还有多少秒
```

### 13.3 交易时段 (UTC)

| 市场 | 开盘 | 收盘 | 午休 | 备选（冬令时） |
|------|------|------|------|---------------|
| US | 13:30 | 20:00 | — | 14:30-21:00 |
| HK | 01:30 | 08:00 | 04:00-05:00 | — |
| Crypto | — | — | 24/7 | — |

### 13.4 数据源

- **US/HK**: `exchange_calendars` 库（含节假日）
- **Crypto**: 永远开放

---

## 14. 市场时段保护

### 14.1 目的

防止在交易时段重启 ws_collector 导致数据丢失。

### 14.2 实现

**脚本**: `scripts/market_open.sh`

```bash
#!/bin/bash
# 返回 0 = 市场开盘（不应重启），1 = 市场关闭（可以重启）
```

**deploy.sh 集成**:
```bash
MARKET_CHECK="$PROD_ROOT/scripts/market_open.sh"
if [ -x "$MARKET_CHECK" ] && "$MARKET_CHECK"; then
    log "⚠️ MARKET OPEN — skipping ws_collector restart"
else
    sudo systemctl restart ws-collector
fi
```

### 14.3 规则

- CD 部署时自动检查，开盘期间跳过 ws_collector 重启
- **手动重启 ws_collector 需老大批准**
- 该保护仅作用于 ws_collector，其他服务（Dashboard, MLflow）不受影响

---

## 15. 生产禁区 (quant-prod)

### 15.1 铁律

1. **`/opt/quant-prod/` 禁止任何直接修改**（包括写文件、跑脚本、改配置、操作 MLflow DB）
2. **生产操作唯一定义**: 数据采集、入库、真实下单的实盘交易
3. **开发操作**: 模型训练、因子计算、回测、回填、数据分析 → 必须在 `/opt/quant-dev/`
4. **流程**: dev → git commit → PR → CI → merge stable → CD → prod

### 15.2 例外

- Live 实验的 paper trading（`mode: live`）允许在 prod 运行（属于实时模拟，非开发）
- 数据采集 cron 任务允许在 prod 执行
- Dashboard 服务允许在 prod 运行

### 15.3 通用红线

- 禁止擅自删除文件
- 禁止擅自修改/删除/写入 GCP 资源
- 禁止擅自修复 Bug（先排查，提出方案，等批准再修）
- 代码任务必须先描述计划、等批准再动手
- 系统配置修改必须先展示改动、等审阅再执行
- 标的覆盖数量以 `config/symbols.yaml` 为准，不得自设

---

## 16. 管理平台 (Admin Platform)

### 16.1 概述

统一管理平台，在一个前端页面上操作所有量化系统模块，替代 SSH + 命令行。

- **地址**: `http://localhost:8091`（公网通过 cloudflared tunnel）
- **架构**: React 18 + Ant Design Pro (Vite/TS) → FastAPI (:8091) → SQLAlchemy/SQLite → Worker
- **认证**: 无（cloudflared 隧道 + 防火墙保护）
- **部署**: `systemctl [start|stop|restart] quant-admin quant-admin-worker`

### 16.2 模块功能

#### 实验管理

| 功能 | 操作 |
|------|------|
| 实验列表 | 查看所有实验、状态、当前 run、PID |
| 注册实验 | 表单填写 type/market/strategy/version/config → 自动生成 ID |
| 启动/停止/重启 | 按钮触发 → worker 执行 `exp_cli` |
| 实验详情 | Drawer: 基本信息、权益图（复用 Dashboard）、持仓、交易、run 历史 |
| 清空实验 | 一键清 BQ + state + runs（需二次确认） |

#### 数据采集

| 功能 | 操作 |
|------|------|
| ws_collector 状态 | 进程状态、最后心跳 |
| 启停 ws_collector | 按钮触发 |
| 📊 数据地图 | 所有 BQ 表：表名、行数、最近写入、Schema（点击展开） |
| 数据回填 | 选择 market + 日期范围 → worker 异步执行 |
| F10 采集监控 | 采集器名称、是否运行中 |

#### 日志浏览

| 功能 | 操作 |
|------|------|
| 模块筛选 | `collector / live / factor / cron / train / ...` |
| 级别筛选 | ERROR / WARNING / INFO / DEBUG |
| 实时 tail | WebSocket 推送新日志行 |
| 搜索 | 关键字 + 时间范围（DatePicker） |

#### Cron 任务管理

| 功能 | 操作 |
|------|------|
| 任务列表 | name, description, schedule, command（39 个任务） |
| 启停开关 | Switch 切换 |
| 立即触发 | 按钮 → worker 执行 |
| 新建/编辑 | Modal 表单：name / description / schedule / command |
| 执行历史 | Drawer 显示 Task 表中最近任务记录 |

**SSOT**: 系统 crontab（`quant` 用户）。管理平台直接读写。

#### 模型 & 策略管理

| 功能 | 操作 |
|------|------|
| 模型列表 | 所有注册模型 + 版本列表（MLflow API） |
| 版本对比 | 选中两个版本 → 对比 RMSE / IC / 特征数 |
| Stage 管理 | Promote to Production / Archive |
| 训练触发 | 选择模型 + 参数 → worker 异步训练 |
| 训练历史 | 每个版本的训练参数和指标 |
| 策略列表 | 浏览所有策略源码 |
| 策略编辑 | 在线编辑（语法高亮）+ 保存 |
| MLflow UI | iframe 嵌入 `http://localhost:5000` |

#### 因子管理

| 功能 | 操作 |
|------|------|
| 因子列表 | 所有因子 + 支持市场 + 状态 + IC |
| 因子详情 | Drawer 显示市场数据覆盖（标的数、日期范围、总数据量） |
| 批量计算 | 选择 source/market/日期范围 → worker |
| 激活/停用 | 切换因子状态 |

### 16.3 任务队列

SQLite Task 表 (`/var/quant/admin.db`) + worker 进程异步执行。

| 字段 | 说明 |
|------|------|
| id | 自增 |
| type | `shell` / `exp_start` / `factor_compute` / ... |
| status | `pending` → `running` → `done` / `failed` |
| params | JSON 参数 |
| result | 执行结果文本 |

Worker 循环轮询 pending → 标记 running → subprocess → 更新 done/failed。

### 16.4 启动/停止

```bash
# 启动
sudo systemctl start quant-admin quant-admin-worker

# 停止
sudo systemctl stop quant-admin quant-admin-worker

# 重启
sudo systemctl restart quant-admin quant-admin-worker

# 前端重建（修改前端代码后）
cd /opt/quant-dev/admin/frontend && npm run build
cp -r dist/* /opt/quant-prod/admin/frontend/dist/
```

### 16.5 注意事项

- 管理平台和 Dashboard (:8090) 是独立服务，互不干扰
- 管理平台直接操作系统 crontab（`quant` 用户），修改立即生效
- Worker 执行的命令运行在 `/opt/quant-prod`，使用 `.venv/bin/python3`
- 前端使用相对路径 API（不需要配置域名）
- admin.db 存储在 `/var/quant/admin.db`
