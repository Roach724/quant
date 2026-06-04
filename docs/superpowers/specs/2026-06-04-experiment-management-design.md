# Experiment Management System — Design Spec

> 2026-06-04 | Status: Draft | Author: Jarvis + 老大

## 1. 问题

当前实验管理存在以下痛点：

| 问题 | 表现 |
|------|------|
| ID 无规范 | `exp1_ml_v2`, `exp3_ml_hk_v3`, `exp4_momentum_hk_v2` 格式不一致 |
| BQ 数据掺杂 | 同一 `exp_id` 重启后新旧数据混在一起 |
| 状态管理弱 | 清状态、改配置、重启之间没有保护机制 |
| 类型混用 | live/paper/prod 实验在同一个页面堆在一起 |

## 2. 目标

- **统一 ID 规范**：`{type}_{market}_{strategy}_v{version}`
- **数据隔离**：每次启动自动生成 `run_id`，BQ 按 run 区分
- **生命周期管理**：pending → running → paused → completed → archived
- **Dashboard 类型隔离**：Live / Paper Run / Prod 分 Tab

## 3. ID 规范

```
格式: {type}_{market}_{strategy}_v{version}

type:     live   (实时模拟)
          paper  (纸交回测)
          prod   (实盘真实交易)

market:   us | hk | crypto

strategy: ml  (MLPredStrategy)
          mom (SimpleMomentum)
          自定义策略缩写

version:  数字，从 1 开始递增

示例:
  live_us_ml_v2      — 美股 ML 策略实时模拟 v2
  paper_hk_mom_v1    — 港股动量回测 v1
  prod_us_ml_v1      — 美股 ML 实盘 v1
```

## 4. 数据模型

### 4.1 Registry (`/var/quant/experiments/registry.json`)

```json
{
  "experiments": {
    "live_us_ml_v2": {
      "id": "live_us_ml_v2",
      "type": "live",
      "market": "us",
      "strategy": "ml",
      "version": 2,
      "status": "running",
      "config_path": "live/configs/exp1_ml_us.yaml",
      "created_at": "2026-06-02T07:00:00Z",
      "current_run": "20260604_130500",
      "runs": [
        {"run_id": "20260604_030000", "started": "...", "ended": "...", "status": "paused"},
        {"run_id": "20260604_130500", "started": "...", "ended": null, "status": "running"}
      ]
    }
  }
}
```

- `id`: 唯一标识，由 type/market/strategy/version 组合生成
- `status`: pending | running | paused | completed | archived | failed
- `current_run`: 当前活跃的 run_id，paused 时为上一轮
- `runs`: 所有历史 run 记录

### 4.2 BQ 表变更

**新表 `experiment_runs`：**
| 列 | 类型 | 说明 |
|----|------|------|
| run_id | STRING | e.g. `20260604_130500` |
| exp_id | STRING | 关联 experiment |
| status | STRING | running / paused / completed / failed |
| started_at | TIMESTAMP | |
| ended_at | TIMESTAMP | |
| base_run | STRING | resume 时的上一轮 run_id |
| notes | STRING | |

**现有表加列：**
- `experiment_equity` + `run_id STRING`
- `experiment_trades` + `run_id STRING`
- `paper_runs` + `run_id STRING`

### 4.3 Run ID 格式

```
YYYYMMDD_HHMMSS

启动时自动生成，不可复用，全局唯一。
```

## 5. ExperimentManager

核心类，管理实验全生命周期。

```python
class ExperimentManager:
    """实验生命周期管理器。"""

    def register(id, type, market, strategy, version, config_path) -> Experiment
    def start(exp_id) -> Run
    def pause(exp_id) -> None
    def resume(exp_id) -> Run
    def stop(exp_id) -> None
    def archive(exp_id) -> None
    def list(status=None) -> list[Experiment]
    def get(exp_id) -> Experiment
    def runs(exp_id) -> list[Run]
```

### 保护机制

| 操作 | 前置条件 | 拒绝时提示 |
|------|----------|-----------|
| register | ID 唯一，type/market/strategy 合法 | "实验已存在" |
| start | status 为 paused/completed/archived | "实验正在运行中" |
| pause | status 为 running | "实验未在运行" |
| resume | status 为 paused，BQ 无残留 run_id | "BQ 数据冲突，请先清理" |
| stop | status 为 running/paused | "实验未在运行" |
| archive | status 为 completed/stopped | "请先停止实验" |

- `resume` 时检测 BQ `experiment_runs` 表中是否已有同名 `run_id`，有则拦截
- 所有操作更新 registry.json + BQ experiment_runs
- `pause` 时保存状态 checkpoint（持仓/资金/风控），调用现有 StateManager

## 6. 实验配置 yaml

`experiment:` 段改为声明元数据，不再嵌 ID：

```yaml
# 原来
experiment:
  id: exp3_ml_hk_v3
  name: "Exp3 — MLPredStrategy hk_tech v3"

# 改为
experiment:
  type: live
  market: hk
  strategy: ml
  version: 3
  name: "MLPredStrategy hk_tech v3"
```

ID 由 ExperimentManager 组合生成（`{type}_{market}_{strategy}_v{version}`），保证一致性。

## 7. CLI

```bash
# 注册
python -m exp register live/us/ml/v2 --config live/configs/exp1_ml_us.yaml

# 生命周期
python -m exp start    live_us_ml_v2
python -m exp pause    live_us_ml_v2
python -m exp resume   live_us_ml_v2
python -m exp stop     live_us_ml_v2
python -m exp archive  live_us_ml_v2

# 查看
python -m exp list
python -m exp show     live_us_ml_v2
python -m exp runs     live_us_ml_v2
```

## 8. Dashboard 适配

### Tab 结构

| Tab | 数据源 | 过滤 |
|-----|--------|------|
| Overview | 所有实验汇总卡片 | 无 |
| Live | live_* | /api/experiments?type=live |
| Paper Run | paper_* | /api/experiments?type=paper |
| Prod | prod_* | /api/experiments?type=prod |
| Pipeline | 不变 | 无 |
| Alerts | 不变 | 无 |

### 实验卡片

```
┌─────────────────────────────┐
│ 🟢 live_hk_ml_v3            │
│ MLPredStrategy hk_tech v3   │
│ Equity: $1,001,759          │
│ Run: #20260604_054326       │
│ [切换到历史 run ▼]          │
└─────────────────────────────┘
```

### Live / Paper Run / Prod Tab

- 顶部 run 选择器，默认最新 run，可切换历史
- 权益/回撤/交易/持仓 全部按 `run_id` 过滤
- paused/completed/failed run 标注状态标签
- Prod Tab 只读：不显示 pause/resume/stop 按钮

### Paper Run Tab 特殊性

- 复用现有 metrics 卡片（Sharpe / MaxDD / CAGR 等）
- 复用现有回测报告展示

### 状态颜色

```
🟢 running    🔵 paused    ✅ completed    🔴 failed    ⬜ archived
```

## 9. 迁移计划

### 现有实验映射

| 现在 | 改为 | 
|------|------|
| `exp1_ml_v2` (us, live) | `live_us_ml_v2` |
| `exp2_simple_momentum` | `live_us_mom_v1` |
| `exp3_ml_hk_v3` (hk, live) | `live_hk_ml_v3` |
| `exp4_momentum_hk_v2` | `live_hk_mom_v2` |

### 迁移步骤

1. 创建 registry.json，注册 4 个实验
2. 迁移脚本：读取旧 BQ experiment_equity/trades → 回填 run_id → 写入 experiment_runs
3. 更新实验配置 yaml（experiment 段改为新格式）
4. 重启 4 个实验
5. 旧 BQ 数据保留不删（exp3_ml_hk, exp4_momentum_hk 等旧 ID 的脏数据）
