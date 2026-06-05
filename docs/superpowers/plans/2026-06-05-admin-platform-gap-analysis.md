# Admin Platform — 补全计划

> 2026-06-05 | 差异分析 + 补全任务

## 差异清单

### 3.1 实验管理

| 功能 | Spec | 实现 | 差距 |
|------|:---:|:---:|------|
| 实验列表 | ✅ | ✅ | — |
| 启动/停止/重启 | ✅ | ✅ | — |
| **注册实验** | ✅ | ❌ | 缺表单：type/market/strategy/version/config 输入 + 自动生成 ID |
| **查看详情** | ✅ | ❌ | 缺详情页：权益图、持仓表、交易表、run 历史（复用 Dashboard API） |
| **清空实验** | ✅ | ❌ | 缺一键清空 BQ+state+runs（需二次确认） |
| PID 显示 | ✅ | ❌ | 列表缺 PID 列 |

### 3.2 数据采集

| 功能 | Spec | 实现 | 差距 |
|------|:---:|:---:|------|
| ws_collector 状态 | ✅ | ✅ | — |
| 启停 ws_collector | ✅ | ✅ | — |
| 数据地图 | ✅ | ✅ | — |
| **数据新鲜度** | ✅ | ❌ | 数据地图表缺 "最后 bar 时间" 列 + 超时高亮 |
| **回填触发** | ✅ | ❌ | 缺：选择 market + 日期范围 → worker 执行 backfill.py |
| **F10 采集监控** | ✅ | ❌ | 缺：各 F10 采集器状态（us_rating_summary, insider_trade 等） |

### 3.3 日志浏览

| 功能 | Spec | 实现 | 差距 |
|------|:---:|:---:|------|
| 模块筛选 | ✅ | ✅ | — |
| 级别筛选 | ✅ | ✅ | — |
| 实时 tail | ✅ | ✅ | — |
| 搜索 | ✅ | ✅ | — |
| **时间范围** | ✅ | ❌ | 缺 DatePicker 时间范围过滤 |

### 3.4 Cron 管理

| 功能 | Spec | 实现 | 差距 |
|------|:---:|:---:|------|
| 任务列表 | ✅ | ✅ | — |
| 立即触发 | ✅ | ✅ | — |
| **启停开关** | ✅ | ❌ | 缺：Switch 组件切换 enabled/disabled（注释/取消注释 crontab 行） |
| **执行历史** | ✅ | ❌ | 缺：cron log 解析显示历史执行结果 |
| **新建/编辑** | ✅ | ❌ | 缺：crontab 表达式编辑器 + 命令行输入表单 |
| **同步检查** | ✅ | ❌ | 缺：比对管理平台配置 vs 系统 crontab |

### 3.5 模型 & 策略

| 功能 | Spec | 实现 | 差距 |
|------|:---:|:---:|------|
| 模型列表 | ✅ | ✅ | — |
| 训练触发 | ✅ | ✅ | — |
| 策略列表 + 编辑 | ✅ | ✅ | — |
| MLflow iframe | ✅ | ✅ | — |
| **版本对比** | ✅ | ❌ | 缺：选中两个版本 → 对比 RMSE/IC/特征数/训练时间 |
| **Stage 管理** | ✅ | ❌ | 缺：Promote to Production / Archive 按钮 |
| **训练历史** | ✅ | ❌ | 缺：训练记录列表（版本 ← 参数对应关系） |

### 3.6 因子管理

| 功能 | Spec | 实现 | 差距 |
|------|:---:|:---:|------|
| 因子列表 + 市场 | ✅ | ✅ | — |
| 因子详情 + 覆盖 | ✅ | ✅ | — |
| 批量计算 | ✅ | ✅ | — |
| **注册/注销** | ✅ | ❌ | 缺：激活/停用因子按钮（调 FactorRegistry.deactivate/activate） |
| **因子评估** | ✅ | ❌ | 缺：触发 evaluate → 更新 IC 等指标 |

---

## 补全任务

### Task A: 实验管理补全

**A1. 注册实验表单**
- 在 Experiments 页面加 "注册实验" 按钮 → 弹出 Modal
- 表单：type(Select) / market(Select) / strategy(Input) / version(InputNumber) / config_path(Input)
- 自动预览生成的 ID：`{type}_{market}_{strategy}_v{version}`
- 提交 → `POST /api/admin/experiments/register` → `ExperimentManager.register()`

**A2. 实验详情页**
- 点击实验 ID → 跳转详情页或 Drawer
- 权益图（复用 Dashboard `/api/equity/{exp_id}` → Plotly）
- 持仓表（`/api/experiments/{exp_id}/positions`）
- 交易表（`/api/trades/{exp_id}`）
- Run 历史列表（`/api/experiments/{exp_id}/runs`）

**A3. 清空实验**
- 详情页底部的危险操作区
- "清空所有数据" 按钮 → Popconfirm 二次确认
- `POST /api/admin/experiments/{exp_id}/clear` → worker 执行：删 BQ experiment_equity + experiment_trades + 清 state 文件 + 重置 registry runs

**A4. PID 列**
- 实验列表加 PID 列（已有数据源，只需加列）

### Task B: 数据采集补全

**B1. 回填触发**
- 数据页面加 "数据回填" 卡片
- 表单：market(Select) / 日期范围(DatePicker) / "开始回填" 按钮
- `POST /api/admin/data/backfill` → worker → `collectors/backfill.py --market X --start Y --end Z`

**B2. F10 采集监控**
- 数据页面加 "F10 采集器" 卡片
- 列表：采集器名 / 最后运行时间 / 状态
- 遍历系统 crontab 中 `collect_*` 任务，解析最后执行日志

### Task C: 日志浏览补全

**C1. 时间范围过滤**
- LogViewer 加 DatePicker 范围选择器
- API 加 `start/end` 参数 → 过滤日志行 ts 字段

### Task D: Cron 管理补全

**D1. 启停开关**
- 每行加 Switch，切换 enabled/disabled
- enabled=true → 把注释行去掉 # 恢复
- enabled=false → 在行首加 # 注释
- `PUT /api/admin/cron/{index}/toggle`

**D2. 新建/编辑任务**
- "新建任务" 按钮 → Modal
- 表单：schedule(Input + cron 表达式预览) + command(Input)
- 提交 → `POST /api/admin/cron/add`

**D3. 执行历史**
- 每行 "历史" 按钮 → Drawer
- 查询 cron log 或系统日志中该命令的最近执行记录
- `GET /api/admin/cron/{index}/history`

### Task E: 模型 & 策略补全

**E1. 版本对比**
- 模型列表每行展开或 "对比" 按钮
- 选中两个版本 → 对比表格：RMSE / IC / 特征数 / 训练时间
- 数据来源：MLflow `get_run(run_id).data.metrics`

**E2. Stage 管理**
- 每个版本旁加下拉菜单：Promote to Production / Archive
- `POST /api/admin/models/{name}/version/{v}/stage` → MLflow transition_model_version_stage

**E3. 训练历史**
- 模型详情 Drawer → 训练记录列表
- 显示每次训练的 run_id / 参数 / metrics / 时间

### Task F: 因子管理补全

**F1. 注册/注销因子**
- 每行加 Switch 或按钮：激活 / 停用
- `POST /api/admin/factors/{factor_id}/toggle`
- 调 FactorRegistry.deactivate / 或更新状态

**F2. 因子评估触发**
- 详情 Drawer 加 "运行评估" 按钮
- `POST /api/admin/factors/{factor_id}/evaluate`
- worker → factor.evaluate() 已实现

---

## Task Summary

| ID | 模块 | 任务 | 优先级 |
|----|------|------|:---:|
| A1 | 实验 | 注册实验表单 | P0 |
| A2 | 实验 | 实验详情页 | P0 |
| A3 | 实验 | 清空实验 | P1 |
| A4 | 实验 | PID 列 | P2 |
| B1 | 数据 | 回填触发 | P1 |
| B2 | 数据 | F10 采集监控 | P2 |
| C1 | 日志 | 时间范围过滤 | P2 |
| D1 | Cron | 启停开关 | P1 |
| D2 | Cron | 新建/编辑 | P1 |
| D3 | Cron | 执行历史 | P2 |
| E1 | 模型 | 版本对比 | P1 |
| E2 | 模型 | Stage 管理 | P1 |
| E3 | 模型 | 训练历史 | P2 |
| F1 | 因子 | 注册/注销 | P1 |
| F2 | 因子 | 因子评估触发 | P2 |

**总计：16 个补全任务。P0: 2, P1: 8, P2: 6**
