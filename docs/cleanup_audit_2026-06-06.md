# /opt/quant-prod 清理审计报告

> 生成: 2026-06-06 14:47 UTC
> 范围: `/opt/quant-prod` 全部 478 个文件
> 原则: 所有改动在 dev 完成 → PR → CI → merge → CD 部署

---

## 一、✅ 可直接删除（纯删，零代码改动）

### 1.1 历史文档 (~45 个文件)

```
docs/superpowers/plans/     (25 个历史规划文档, 2026-05-14 ~ 06-05)
docs/superpowers/specs/     (20 个历史设计文档, 2026-05-14 ~ 06-05)
docs/backfill-tracker.md
docs/backfill_tracker.md
docs/plans/2026-06-05-ml-subsystem-upgrade.md
docs/research/crypto-strategy-design-2026-05-19.md
docs/research/futu-api-1-api-2-api-ethereal-falcon.md
docs/issues/2026-06-05-ws-collector-hang.md
```

### 1.2 配置文件垃圾

```
# .del / .bak 文件 (15 个)
live/configs/debug_us_ml.yaml.del
live/configs/exp1_ml_us.yaml.del
live/configs/exp2_momentum_us.yaml.del
live/configs/exp3_ml_hk.yaml.del
live/configs/exp4_momentum_hk.yaml.del
live/configs/live_us_ml_test1.yaml.del
live/configs/live_us_ml_test99.yaml.del
live/configs/live_us_mom.yaml.bak
live/configs/live_us_mom.yaml.del
live/configs/live_us_mom_20260605.yaml.bak
live/configs/live_us_mom_20260605.yaml.del
live/configs/live_us_test_del.yaml.del
live/configs/paper_hk_baseline.yaml.bak
live/configs/paper_simple.yaml.del
live/configs/simple_momentum.yaml.del

# 未使用的活跃配置 (7 个)
live/configs/lgb_ml.yaml
live/configs/live_multi_day_us.yaml
live/configs/live_paper_ml_us.yaml
live/configs/live_paper_us.yaml
live/configs/live_us.yaml
live/configs/live_us_mom.yaml
live/configs/paper_simple.yaml

# ML 配置垃圾
ml/configs/lgb_us_example.yaml
ml/configs/us_baseline.yaml.bak
```

### 1.3 死代码 — collectors/adapters/ (7 个)

```
collectors/adapters/akshare_hk_adapter.py
collectors/adapters/akshare_us_adapter.py
collectors/adapters/alpaca_adapter.py
collectors/adapters/crypto_binance_adapter.py
collectors/adapters/crypto_futu_adapter.py
collectors/adapters/yfinance_adapter.py
collectors/adapters/yfinance_hk_adapter.py
collectors/adapters/base.py
```

### 1.4 死代码 — collectors/ (3 个)

```
collectors/main.py           # 旧 Cloud Run 入口，已被 compute_daily_bars.py 取代
collectors/backfill.py       # 自引用死代码
collectors/ob_collector.py   # 自引用死代码
collectors/requirements.txt  # 子 requirements，未引用
```

### 1.5 死代码 — bigquery_loader/ (1 个)

```
bigquery_loader/load_futu_factors.py  # 自引用死代码
```

### 1.6 遗留代码 — experiment/ (5 个)

```
experiment/runner.py              # 旧实验 runner，只有 run_paper.py 引用
experiment/investment_record.py   # 同上
experiment/config_factor_ic.yaml  # 只被死脚本 run_w3_experiment.py 引用
experiment/config_w2.yaml         # 只被死脚本 run_w2_experiment.py 引用
experiment/config_w3_5m.yaml      # 同上
```

### 1.7 遗留代码 — execution/ (4 个)

```
execution/twap.py       # TWAP 执行器，从未被 live runner 使用
execution/vwap.py       # VWAP 执行器，同上
execution/protocol.py   # 基类协议，只被 twap/vwap 引用
execution/__init__.py
```

### 1.8 遗留代码 — paper_run/ (5 个)

```
paper_run/__init__.py
paper_run/__main__.py
paper_run/cli.py
paper_run/metrics.py
paper_run/runner.py
```

### 1.9 一次性脚本 — scripts/ (12 个)

```
scripts/train_us_tech_v1.py          # ⚠️ 见第二部分，需先改 admin/server.py
scripts/train_us_tech_v1_explicit.py # ⚠️ 同上
scripts/train_hk_tech_v1.py          # ⚠️ 同上
scripts/run_w2_experiment.py
scripts/run_w3_experiment.py
scripts/evaluate_all_factors.py
scripts/evaluate_f10_factors.py
scripts/hk_backfill_1d.py
scripts/migrate_experiments.py
scripts/init_factor_registry.py
scripts/load_f10_bq.py
scripts/backfill_chain.sh
scripts/run_paper_momentum.sh
```

### 1.10 遗留代码 — 其他 (10 个)

```
run_paper.py                      # 旧入口
live/exp_cli.py                   # 已被 Admin 实验管理取代
dashboard/server.py               # 旧 Dashboard API (已迁移到 Admin :8091)
dashboard/api.py                  # 旧 Dashboard API 子模块
paper/config.yaml                 # 只被 run_paper.py 引用
admin/pipeline.py                 # 与 ml/pipeline.py 重复
docker/start_collect.sh           # 旧 Docker 入口
models/momentum_lgbm/v1/config.yaml  # 旧模型目录
CLAUDE.md                         # AI 上下文文件
MEMO.md                           # AI 上下文文件
HANDBOOK.md                       # 系统和运维手册

# 测试
tests/test_paper_metrics.py       # 测试已废弃的 paper_run
```

### 1.11 历史输出

```
output/live/  中除 state/ 子目录外的所有历史实验运行子目录 (~35 个)
```

---

## 二、⚠️ 需先改代码再删

| 文件 | 阻塞原因 | 需要的代码改动 |
|------|---------|--------------|
| `scripts/train_us_tech_v1.py` | `admin/server.py:1021` 引用 | 改用 `ml/pipeline.py` 调用 |
| `scripts/train_us_tech_v1_explicit.py` | `admin/server.py:1021` 引用 | 同上 |
| `scripts/train_hk_tech_v1.py` | `admin/server.py:1022` 引用 | 同上 |
| `oms/alerting.py` | `oms/__init__.py` 导出 | 清理 `__init__.py` 导出 |
| `oms/risk_monitor.py` | `oms/__init__.py` 导出 | 同上 |
| `oms/broker/crypto_broker.py` | 测试引用 | 同步删测试 |
| `oms/broker/crypto_futu_broker.py` | 测试引用 | 同上 |
| `oms/broker/market_data.py` | 测试引用 | 同上 |

---

## 三、⚠️ 需主观判断

| 文件/目录 | 问题 |
|-----------|------|
| `.github/workflows/ci.yml` | CI 还在用吗？ |
| `.github/workflows/deploy.yml` | CD 还在用吗？ |
| `terraform/startup.sh` | VM 重建时还需要吗？内容已过时(硬编码旧路径) |
| `docs/Futu-API-Doc-zh-Python.md` | API 文档还有参考价值吗？ |
| `docs/manual/01-investing.md` | 运维手册还在维护吗？ |
| `docs/manual/02-research.md` | 运维手册还在维护吗？ |
| `docs/manual/03-operations.md` | 运维手册还在维护吗？ |
| `docs/knowledge_base/admin-frontend.md` | 前端踩坑记录还在维护吗？ |
| `docs/KNOWN_ISSUES.md` | 已知问题列表是否过时？ |
| `docs/TASKS.md` | 任务清单是否过时？ |
| `docs/logging.md` | 日志规范文档 |
| `bigquery_loader/requirements.txt` | Cloud Run 部署还需要吗？ |
| `models_artifacts/` 旧 run | MLflow 旧运行记录，可通过 MLflow UI 清理 |
| `output/live/` 根目录 | 活跃输出路径，不能删根，但可清历史子目录 |
| `.pytest_cache/` | 自动生成，可随时删 |

---

## 四、❌ 必须保留（容易误判）

这些文件不在第一眼"活跃清单"中，但被活跃代码引用：

| 文件 | 被谁引用 | 用途 |
|------|---------|------|
| `dashboard/observer.py` | `live/runner.py` | DashboardObserver 写 BQ |
| `experiment/tracker.py` | `live/config.py` | ExperimentTracker |
| `paper/strategies.py` | `live/runner.py` | SimpleMomentum 策略回退 |
| `paper/market.py` | `strategies/SectorRotation.py` | UniverseBuilder |
| `paper/__init__.py` | 多处 | 包导出 |
| `collectors/schema.py` | 8 个子 collector | 数据模型 |
| `collectors/storage.py` | 8 个子 collector | 存储抽象 |
| `collectors/futu_collector_base.py` | 8 个子 collector | 基类 |
| `collectors/adapters/_futu_base.py` | 6 Futu adapter | 基类 |
| `collectors/adapters/futu_*_adapter.py` ×6 | `fundamental_collector.py` | F10 数据采集 |
| `collectors/adapters/futu_stock_adapter.py` | `paper/market.py` | 股票数据 |
| `collectors/*_collector.py` ×8 | `collect_futu_factors.py` ×20 cron | 因子采集 |
| `bigquery_loader/main.py` | systemd + terraform | BQ 写入 |
| `config/cron_registry.json` | Admin cron 管理 | cron 注册表 |
| `collectors/__init__.py` | 包 | Python 包 |
| `bigquery_loader/__init__.py` | 包 | Python 包 |
| `admin/__init__.py` | 包 | Python 包 |
| `dashboard/__init__.py` | 包 | Python 包 |
| `experiment/__init__.py` | 包 | Python 包 |
| `tests/test_experiment_manager.py` | 测试 | 活跃代码测试 |
| `tests/collectors/test_futu_collector_base.py` | 测试 | 活跃代码测试 |

---

## 执行建议

1. **先做一** — 纯删，零风险，git rm 即可
2. **再做二** — 小改 admin/server.py + oms/__init__.py，然后删
3. **最后三** — 你逐个判断后决定
4. **四不动** — 标记保留，避免误删
