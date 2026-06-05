# ML 子系统升级方案 — 配置驱动训练+调优+评估

> 2026-06-05 | Jarvis | 更新: 加数据集注册模块

## 目标

将 ML 子系统整合为配置驱动的统一流程：**数据集注册 → 配置训练 → 调优 → 评估 → 注册**。

---

## 1. 架构总览

管理平台 Models 页拆为三个子 Tab：

```
Models & ML
├── 数据集      — 注册因子数据集，选择因子+label，生成 BQ 表
├── ML 配置     — YAML 配置文件 CRUD，点击注册到模型中心
└── 模型中心    — 训练、MLflow 模型列表、版本、指标、绑定数据集
```

数据流：

```
数据集注册           ML 配置                       模型中心(train)
┌──────────┐       ┌──────────────┐  register    ┌──────────────┐
│ 选因子    │       │ dataset:     │──────────────→│ TrainPipeline │
│ 选label  │──→BQ  │   us_tech_v1 │              │  ↓            │
│ 时间范围  │  表   │ model:       │              │ DatasetManager│
│ 生成BQ表  │       │   lightgbm   │              │ ModelTrainer  │
└──────────┘       │ tuning: ...  │              │ OptunaTuner   │
                   └──────────────┘              │ ModelRegistry │
                                                 └──────────────┘
```
└──────────┘      │ tuning: ...  │      │ OptunaTuner   │
                  └──────────────┘      │ ModelRegistry │
                                        └──────────────┘
```

---

## 2. 数据集注册模块

### 2.1 元数据存储

数据集注册信息存 `admin.db`（sqlite），新建表：

```sql
CREATE TABLE ml_datasets (
    id INTEGER PRIMARY KEY,
    name TEXT UNIQUE NOT NULL,          -- e.g. "us_tech_v1"
    market TEXT NOT NULL,               -- "us" | "hk"
    label TEXT NOT NULL,                -- "fwd_ret_5d" | "fwd_ret_20d"
    factor_ids TEXT NOT NULL,           -- JSON array of factor IDs
    train_range TEXT NOT NULL,          -- "2020-01-01,2023-12-31"
    val_range TEXT NOT NULL,            -- "2024-01-01,2024-12-31"
    test_range TEXT NOT NULL,           -- "2025-01-01,2025-12-31"
    bq_table TEXT,                      -- BQ 表全名，生成后填充
    status TEXT DEFAULT 'registered',   -- registered | generating | ready | failed
    row_count INTEGER,                  -- 总行数
    created_at TEXT,
    updated_at TEXT
);
```

### 2.2 生成后的 BQ 表结构

数据集实际数据存在 BQ 表 `quant.ml_dataset_{name}` 中：

```
┌────────┬────────────┬──────────┬───────┬───────┬─────┬───────────┬─────┐
│ symbol │ date       │ split    │ timestamp │ factor_1 │ factor_2 │ ... │ label │
├────────┼────────────┼──────────┼───────┼───────┼─────┼───────────┼─────┤
│ AAPL   │ 2020-01-02 │ train    │ 2020-01-02 00:00:00 │ 0.012 │ -0.003 │ ... │ 0.008 │
│ AAPL   │ 2020-01-03 │ train    │ ... │
│ ...    │ ...        │ val      │ ... │
│ AAPL   │ 2025-06-01 │ test     │ ... │
└────────┴────────────┴──────────┴───────┴───────┴─────┴───────────┴─────┘
```

- `split`: `train` / `val` / `test`
- `timestamp`: 原始 bar 时间戳
- 列名 = factor_id 去掉 market 前缀（如 `us_ret_5d` → `ret_5d`）
- `label` 列 = 用户选择的 label

### 2.3 API

| 端点 | 说明 |
|------|------|
| `GET /api/admin/ml/datasets` | 列出所有数据集 |
| `POST /api/admin/ml/datasets` | 注册（不生成 BQ 表） |
| `GET /api/admin/ml/datasets/{id}/factors` | 获取该市场可选因子列表（从 BQ factor_values 查） |
| `POST /api/admin/ml/datasets/{id}/generate` | 生成/重建 BQ 表 |
| `DELETE /api/admin/ml/datasets/{id}` | 删除注册 + BQ 表 |
| `GET /api/admin/ml/datasets/{id}` | 详情（含 bq_table, row_count, status） |

### 2.4 前端——数据集 Tab

**列表视图：**
```
┌──────────────────────────────────────────────────────────────┐
│ [+ 新建数据集]                                                │
│                                                               │
│ 名称        市场  因子数  Label        BQ表          行数  操作  │
│ us_tech_v1  US    39     fwd_ret_5d   quant.ml_ds_.. 12M  [生成] [🗑] │
│ hk_val_v1   HK    25     fwd_ret_5d   —              —    [生成] [🗑] │
└──────────────────────────────────────────────────────────────┘
```

**新建 Modal：**
```
┌─ 新建数据集 ──────────────────────────────────┐
│ 名称: [us_tech_v2          ]                  │
│ 市场: [US ▼]                                   │
│ Label: [fwd_ret_5d ▼]                         │
│                                                │
│ ── 因子选择 ──                                  │
│ [全选] [搜索...]                                │
│ ☑ us_ret_5d      日收益       daily    95%     │
│ ☑ us_ret_20d     20日收益    daily    95%     │
│ ☑ us_vol_5d      5日波动     daily    95%     │
│ ☐ us_rsi_14      RSI14       daily    95%     │
│ ... (从 BQ factor_values 读取)                  │
│                                                │
│ ── 时间范围 ──                                  │
│ 训练集: [2020-01-01] ~ [2023-12-31]             │
│ 验证集: [2024-01-01] ~ [2024-12-31]             │
│ 测试集: [2025-01-01] ~ [2025-12-31]             │
│                                                │
│ [取消]  [创建]                                   │
└────────────────────────────────────────────────┘
```

**生成按钮逻辑：**
- 未生成：表不存在 → 创建 BQ 表，写入数据，更新 `bq_table` + `status=ready`
- 已生成：表存在 → 确认覆盖 → DROP + CREATE 全量重建

**删除：** 二次确认 → 删 BQ 表 + 删 sqlite 记录

---

## 3. ML 配置

### 3.1 职责

ML 配置只管 YAML 文件的增删改查，不直接触发训练。

- **注册**：点击后在模型中心创建一个条目（记录 config_path 等元数据），后续训练在模型中心触发
- **删除**：先检查 MLflow 中 `registry.model_name` 下是否有已注册的模型版本
  - 有 → 提示"请先在 MLflow 中删除该模型的所有版本"，拒绝删除
  - 无 → 删除配置文件 + 模型中心条目

### 3.2 配置存储

配置文件存磁盘 `ml/configs/{name}.yaml`，元数据在 sqlite：

```sql
CREATE TABLE ml_configs (
    id INTEGER PRIMARY KEY,
    name TEXT UNIQUE NOT NULL,
    description TEXT,
    config_path TEXT NOT NULL,         -- "ml/configs/lgb_us_v1.yaml"
    created_at TEXT,
    updated_at TEXT
);
```

### 3.2 配置模板 (`ml/configs/lgb_us_v1.yaml`)

```yaml
# ── 基本信息 ──
name: lgb_us_v1
description: "LightGBM US 多因子模型"

# ── 数据 ──
data:
  dataset: us_tech_v1                # ← 数据集注册名称，TrainPipeline 自动从 BQ 加载
  label: fwd_ret_5d

# ── 模型 ──
model:
  type: lightgbm
  params:
    objective: regression
    metric: rmse
    boosting_type: gbdt
    num_leaves: 31
    learning_rate: 0.05
    feature_fraction: 0.8
    bagging_fraction: 0.7
    bagging_freq: 5
    min_data_in_leaf: 100
    lambda_l1: 0.5
    lambda_l2: 0.5
    num_boost_round: 2000
    early_stopping_rounds: 100
    seed: 42

# ── 调优（可选） ──
tuning:
  enabled: true
  n_trials: 50
  direction: maximize
  metric: val_ic
  search_space:
    num_leaves:    { type: int, low: 8, high: 128 }
    learning_rate: { type: loguniform, low: 0.01, high: 0.2 }
    feature_fraction: { type: uniform, low: 0.4, high: 1.0 }
    lambda_l1:     { type: loguniform, low: 1e-4, high: 10.0 }

# ── 评估 ──
evaluation:
  metrics: [rmse, rank_ic]
  save_top_features: 20

# ── 注册 ──
registry:
  model_name: us_tech
  stage: production
  tags: { market: us, factor_source: tech }
```

### 3.3 前端——ML 配置 Tab

```
┌──────────────────────────────────────────────────────────────┐
│ [+ 新建配置]                                                  │
│                                                               │
│ 配置名         描述                数据集          [✏️] [📋注册] [🗑] │
│ lgb_us_v1   LightGBM US 多因子   us_tech_v1     [✏️] [📋注册] [🗑] │
│ ridge_hk_v1 Ridge HK 基线        hk_val_v1      [✏️] [📋注册] [🗑] │
└──────────────────────────────────────────────────────────────┘
```

- **新建/编辑**：YAML 编辑器 Drawer
- **📋注册**：读取配置 `registry.model_name`，在模型中心创建条目，关联 config_path
- **🗑删除**：前置检查 → `GET MLflow /model-versions/search?name={registry.model_name}`
  - 有版本 → 弹窗提示"该模型下有 N 个版本，请先在 MLflow 中删除所有版本"，拒绝
  - 无版本 → 删除 YAML 文件 + sqlite 记录

### 3.4 API

| 端点 | 说明 |
|------|------|
| `GET /api/admin/ml/configs` | 列出所有 ML 配置 |
| `PUT /api/admin/ml/configs/{name}` | 创建/编辑配置 |
| `DELETE /api/admin/ml/configs/{name}` | 删除配置（含 MLflow 版本检查） |
| `GET /api/admin/ml/configs/{name}` | 查看配置内容 |
| `POST /api/admin/ml/configs/{name}/register` | 注册到模型中心 |

---

## 4. 模型中心

### 4.1 职责

- 从 ML 配置注册的条目列表
- 点击"训练"触发 TrainPipeline
- 展示 MLflow 已注册模型及版本、指标
- 显示绑定的数据集

### 4.2 前端——模型中心 Tab

```
┌──────────────────────────────────────────────────────────────────────┐
│ 模型名称    版本    Stage       数据集         RMSE    ICIR   [🏃训练]  │
│ us_tech     3      Production  us_tech_v1    0.1564  0.42   [🏃训练]  │
│ us_tech     2      Archived    us_tech_v1    0.1823  0.35   [🏃训练]  │
│ hk_tech     3      Production  hk_val_v1     0.0655  0.51   [🏃训练]  │
└──────────────────────────────────────────────────────────────────────┘
```

- **列出模型中心条目**（来自 sqlite `ml_configs` 中已注册的 + MLflow 模型信息合并）
- **绑定数据集**：`ml_configs.yaml → data.dataset` 字段
- **🏃训练按钮**：Popconfirm（含"是否调优"Switch）→ 提交 Task → TrainPipeline
  - 训练走 task queue，显示进度
  - 完成后自动注册到 MLflow，模型中心刷新
- **点开展开**：版本历史 + 特征重要性图表（现有功能保持）

### 4.3 API

| 端点 | 说明 |
|------|------|
| `POST /api/admin/ml/train` | 提交训练 `{config_name, skip_tuning}` → task_id |
| `GET /api/admin/ml/train/{task_id}` | 查询进度/结果 |
| `GET /api/admin/ml/center` | 模型中心列表（合并 configs + MLflow 信息） |
| `DELETE /api/admin/ml/center/{name}` | 删除模型中心条目（同步删配置） |

---

## 5. 训练执行

### 5.1 TrainPipeline

```python
class TrainPipeline:
    def __init__(self, config_path: str): ...
    def run(self, skip_tuning: bool = False) -> dict:
        # 1. 读配置 YAML
        # 2. data.dataset → 查 ml_datasets 表 → 拿到 bq_table
        # 3. 从 BQ 表加载: WHERE split='train' / 'val' / 'test'
        # 4. 如果 tuning.enabled: Optuna 调参
        # 5. 用最优参数训练最终模型
        # 6. 评估（RMSE + IC + ICIR）
        # 7. 注册到 MLflow
        # 返回 { model_name, version, metrics, features, duration }
```

---

## 6. 前端 Models 页总结构

```
Models & ML
├── 数据集        ← 新增
│   ├── 列表 + 新建 Modal（选因子/label/时间范围）
│   ├── 生成 BQ 表 / 删除
│   └── 已生成→显示 bq_table 名 + 行数
├── ML 配置       ← 新增
│   ├── 列表 + 新建/编辑 YAML + 注册按钮
│   └── 删除→检查 MLflow 模型版本
└── 模型中心      ← 重构（原"模型注册"）
    ├── 列表 + 训练按钮 + 绑定数据集
    └── 展开→版本历史 + 特征重要性（现有功能）
```

---

## 7. 实施顺序

| 步骤 | 内容 | 工作量 |
|------|------|--------|
| 1 | sqlite 加 `ml_datasets` + `ml_configs` 表 | 小 |
| 2 | Admin API — 数据集 CRUD + generate + 因子查询 | 中 |
| 3 | 数据集生成逻辑（BQ 读写） | 中 |
| 4 | Admin API — ML 配置 CRUD（含注册+删除检查） | 中 |
| 5 | Admin API — 模型中心列表 + 训练提交/查询 | 中 |
| 6 | `ml/pipeline.py` — TrainPipeline（用数据集 BQ 表） | 中 |
| 7 | 扩 `ml/tuner.py` 支持 Ridge | 小 |
| 8 | Admin 前端 — 数据集 Tab | 中 |
| 9 | Admin 前端 — ML 配置 Tab | 中 |
| 10 | Admin 前端 — 模型中心 Tab（重构现有 Models 页） | 中 |

**总估：约 6-8 小时。**

---

## 8. 对现有系统的影响

- `ml/trainer.py` — 加 `load_from_dataset(name)` 方法，旧接口保留
- `ml/tuner.py` — 加 Ridge，旧接口保留
- `ml/registry.py` — 不变
- `ml/datasets.py` — DatasetManager 集成到"数据集生成"流程
- `scripts/train_*.py` — 逐步废弃
- MLflow — 不变
- Admin worker — 新增 task 类型 `ml_train`, `ml_dataset_generate`
