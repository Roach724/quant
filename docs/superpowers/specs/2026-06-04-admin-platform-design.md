# Quant Admin Platform — Design Spec

> 2026-06-04 | Status: Draft | Author: Jarvis + 老大

## 1. 目标

构建统一管理平台，在一个前端页面上操作所有量化系统模块，替代 SSH + 命令行 + 多个独立工具的管理方式。

## 2. 架构

```
┌─────────────────────────────────────────────────┐
│        React Admin (Vite + TypeScript)          │
│          Ant Design Pro Layout                   │
│  ┌──────┬──────┬──────┬──────┬──────┬──────┐   │
│  │实验  │数据  │日志  │Cron │模型  │因子  │   │
│  └──────┴──────┴──────┴──────┴──────┴──────┘   │
└──────────────────┬──────────────────────────────┘
                   │ REST API (:8091)
┌──────────────────▼──────────────────────────────┐
│           admin/server.py (FastAPI)              │
│          SQLAlchemy ORM → SQLite                 │
│          Task 表 + 异步任务队列                   │
└──────────────────┬──────────────────────────────┘
                   │
┌──────────────────▼──────────────────────────────┐
│           admin/worker.py                        │
│   后台轮询 pending task → subprocess → 更新状态  │
└──────────────────┬──────────────────────────────┘
                   │
┌──────────────────▼──────────────────────────────┐
│  现有模块: exp_cli, bq_writer, ws_collector,    │
│  backfill, systemctl, MLflow, ExperimentManager  │
└─────────────────────────────────────────────────┘
```

- 前端：React 18 + Vite + TypeScript + Ant Design Pro
- 后端：FastAPI (:8091)，独立于 Dashboard (:8090)
- 数据库：SQLite（任务表 + 配置表）
- 认证：暂不加，cloudflared 隧道 + 防火墙保护
- 任务执行：异步 subprocess + SQLite 任务队列 + worker 进程

## 3. 模块定义

### 3.1 实验管理（Priority 1）

**功能列表：**

| 功能 | 操作 | 后端实现 |
|------|------|---------|
| 实验列表 | 查看所有实验、状态、当前 run、PID | `ExperimentManager.list()` |
| 注册实验 | 表单填写 type/market/strategy/version/config → 生成 ID | `ExperimentManager.register()` |
| 启动 | 按钮 → 后台 nohup + 记录 PID | worker → `exp_cli start` |
| 停止 | 按钮 → kill PID + 更新状态 | worker → `exp_cli stop` |
| 重启 | 按钮 → stop + start 原子操作 | worker → `exp_cli restart` |
| 查看详情 | 权益图、持仓、交易、run 历史 | 复用 Dashboard API |
| 清空实验 | 一键清 BQ + state + runs（需二次确认） | worker → 清理脚本 |

### 3.2 数据采集（Priority 2）

**功能列表：**

| 功能 | 操作 | 后端实现 |
|------|------|---------|
| ws_collector 状态 | 进程状态、最后心跳时间 | `systemctl status` + 日志解析 |
| 启停 ws_collector | 按钮触发，市场时段弹警告 | worker → `systemctl restart/stop` |
| 数据新鲜度 | 各 BQ 表最后 bar 时间 | `MAX(timestamp)` per table |
| 📊 数据地图 | 所有 BQ 表：表名、行数、大小、描述、最近写入、schema | `INFORMATION_SCHEMA` |
| 回填触发 | 选择标的 + 日期范围 → 后台执行 | worker → `backfill.py` |
| F10 采集监控 | 各采集器最后运行时间 | 系统 crontab 日志 |

**数据地图表结构：**

| 列 | 来源 | 示例 |
|----|------|------|
| 表名 | TABLES.table_name | `us_bars_5m` |
| 行数 | TABLES.row_count | 12,345,678 |
| 大小 | TABLES.size_bytes | 2.3 GB |
| 描述 | TABLES 注释 | "美股 5 分钟 K 线" |
| 最近写入 | MAX(timestamp) | 2026-06-04 16:00 UTC |
| Schema | COLUMNS 列表 | symbol, timestamp, open, high, low, close, volume |

### 3.3 日志浏览（Priority 3）

| 功能 | 操作 | 后端实现 |
|------|------|---------|
| 模块筛选 | ws_collector / live / factor / cron / train | 读取 `/var/log/quant/prod/{module}/` |
| 级别筛选 | ERROR / WARNING / INFO / DEBUG | JSON 日志 level 字段过滤 |
| 实时 tail | WebSocket 推送新日志行 | `tail -f` + WebSocket |
| 搜索 | 关键字 + 时间范围 | grep + 时间过滤 |

### 3.4 Cron 任务管理（Priority 4）

**角色：** 管理平台只做管理界面，底层还是系统 cron 执行。

| 功能 | 操作 | 后端实现 |
|------|------|---------|
| 任务列表 | 名称、schedule、最近执行、状态 | 读系统 crontab |
| 启停任务 | 开关按钮 | 注释/取消注释 crontab 行 |
| 立即触发 | 手动执行一次 | worker → `run-parts` |
| 执行历史 | 成功/失败、耗时、输出 | cron log |
| 新建/编辑 | crontab 表达式编辑器 | 写入 crontab |
| 同步检查 | 管理平台配置 vs 系统 crontab 比对 | diff |

### 3.5 模型 & 策略管理（Priority 5）

| 功能 | 操作 | 后端实现 |
|------|------|---------|
| 模型列表 | 所有注册模型 + 版本列表 | MLflow API |
| 版本对比 | RMSE / IC / 特征数 / 训练时间 | MLflow get_model_version |
| Stage 管理 | Promotion / Archive | MLflow transition_model_version_stage |
| 训练触发 | 选择模型 + 参数 → 后台执行 | worker → train_*.py |
| 训练历史 | 版本→参数对应关系 | MLflow run 查询 |
| **策略列表** | 浏览所有策略 + 源码查看 + 参数配置 | 读 `strategies/` 目录 |
| **策略编辑** | 在线编辑代码（语法高亮），保存 → 触发验证 | 文件写入 + worker 回测 |
| MLflow UI | iframe 嵌入 `http://localhost:5000` | iframe |

### 3.6 因子管理（Priority 6）

**因子列表：**

| 列 | 来源 | 示例 |
|----|------|------|
| 因子 ID | FactorRegistry | `us_ret_5d` |
| 名称 | FactorRegistry | "5-Day Return" |
| 分类 | FactorRegistry | momentum |
| 状态 | FactorRegistry | active |
| **支持市场** | **新增** | us, hk |
| 最新 IC | FactorRegistry evaluations | 0.045 |

**因子详情 — 数据覆盖：**

| 市场 | 最早 | 最新 | 标的数 | 总数据量 |
|------|------|------|--------|---------|
| us | 2020-01 | 2026-06 | 234 | 12,345,678 |
| hk | 2020-01 | 2026-06 | 270 | 11,857,970 |

（从 `factor_values` 表 GROUP BY 计算）

**操作：**

| 功能 | 操作 | 后端实现 |
|------|------|---------|
| 注册/注销 | 激活/停用因子 | FactorRegistry |
| 批量计算 | 选择因子 + 市场 + 日期 → worker | worker → `compute_factors_batch.py` |
| 因子评估 | 触发 evaluate → 更新 IC 等指标 | FactorRegistry.evaluate() |

## 4. 任务队列设计

**Task 表 (SQLite)：**

| 字段 | 类型 | 说明 |
|------|------|------|
| id | INTEGER PK | 自增 |
| type | STRING | exp_start / exp_stop / factor_compute / model_train / backfill |
| status | STRING | pending / running / done / failed |
| params | JSON | 任务参数 |
| result | TEXT | 执行结果 / 错误信息 |
| created_at | DATETIME | |
| started_at | DATETIME | |
| finished_at | DATETIME | |

**Worker 流程：**

```
while True:
    读 task WHERE status=pending ORDER BY created_at LIMIT 1
    标记 running + started_at
    根据 type 执行对应操作（subprocess / Python import）
    更新 done/failed + result + finished_at
    sleep 2s
```

## 5. 技术栈

| 层 | 技术 | 版本 |
|----|------|------|
| 前端框架 | React | 18+ |
| UI 组件 | Ant Design Pro | 5+ |
| 构建 | Vite | 5+ |
| 语言 | TypeScript | |
| 后端 | FastAPI | |
| ORM | SQLAlchemy | 2+ |
| 数据库 | SQLite | |
| 任务队列 | 自建（worker 轮询 SQLite） | |
| 部署 | systemd + uvicorn (:8091) + nginx | |

## 6. 部署

```bash
# 后端
/opt/quant-prod/.venv/bin/python3 -m uvicorn admin.server:app --host 0.0.0.0 --port 8091

# Worker
/opt/quant-prod/.venv/bin/python3 admin/worker.py

# 前端
cd admin/frontend && npm run build → 部署到 /opt/quant-prod/admin/static/

# 公网访问
cloudflared tunnel --url http://localhost:8091
```

## 7. 与现有系统的关系

| 现有系统 | 管理平台关系 |
|---------|------------|
| Dashboard (:8090) | 并行运行，管理平台复用 Dashboard API（实验详情） |
| ExperimentManager / CLI | 管理平台通过 API 调用，替代手动 CLI |
| ws_collector (systemd) | 管理平台监控状态 + 启停 |
| 系统 crontab | 管理平台只读 + 编辑，底层仍是 cron 执行 |
| MLflow (:5000) | iframe 嵌入 + API 调用 |
| BQ | 数据地图 + 新鲜度查询 |
