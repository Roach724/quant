# Dashboard 整合到管理平台 — Design Spec

> 2026-06-05 | Status: Draft

## 1. 目标

将现有 Dashboard (:8090) 的所有功能整合到管理平台 (:8091)，统一为一个平台。管理平台后端直接代理/实现 Dashboard API，前端用 React 重写 Dashboard 页面。

## 2. 架构

```
管理平台 (:8091)
├── Dashboard Tab
│   ├── Overview     (重写)
│   ├── Live         (重写)
│   ├── Paper Run    (重写)
│   ├── Prod         (重写)
│   ├── Debug        (重写)
│   ├── Pipeline     (重写)
│   └── Alerts      (简化)
├── 实验管理         (已有)
├── 数据采集         (已有)
├── 日志浏览         (已有)
├── Cron 任务        (已有)
├── 模型 & 策略      (已有)
└── 因子管理         (已有)
```

后端：`admin/server.py` 新增 `/api/admin/dashboard/*` 路由族，直接查 BQ，不再经过 Dashboard (:8090)。

## 3. 后端 API

### 3.1 新增 Dashboard API 路由

以下接口从 `dashboard/server.py` 迁移到 `admin/server.py`，路径加 `/admin/dashboard` 前缀：

| 原路径 | 新路径 | 说明 |
|--------|--------|------|
| `GET /api/experiments` | `GET /api/admin/dashboard/experiments` | 实验最新快照 |
| `GET /api/experiments/meta` | `GET /api/admin/dashboard/experiments/meta` | 实验元数据 |
| `GET /api/equity/{exp_id}` | `GET /api/admin/dashboard/equity/{exp_id}` | 权益曲线 |
| `GET /api/trades/{exp_id}` | `GET /api/admin/dashboard/trades/{exp_id}` | 交易记录 |
| `GET /api/experiments/{exp_id}/positions` | `GET /api/admin/dashboard/experiments/{exp_id}/positions` | 当前持仓 |
| `GET /api/experiments/{exp_id}/runs` | `GET /api/admin/dashboard/experiments/{exp_id}/runs` | Run 历史 |
| `GET /api/pipeline` | `GET /api/admin/dashboard/pipeline` | 数据管道健康 |
| `GET /api/market/{market}/{symbol}` | `GET /api/admin/dashboard/market/{market}/{symbol}` | K 线数据 |
| `GET /api/market/symbols/{market}` | `GET /api/admin/dashboard/market/symbols/{market}` | 标的列表 |
| `GET /api/paper-runs` | `GET /api/admin/dashboard/paper-runs` | Paper Run 列表 |
| `GET /api/paper-runs/{run_id}` | `GET /api/admin/dashboard/paper-runs/{run_id}` | Paper Run 详情 |

### 3.2 实现方式

- **直接复制** `dashboard/server.py` 中的函数到 `admin/server.py`
- 用 BQ client 直查（已有 `_get_bq()` 或创建新的）
- 保持时区修正逻辑不变（HK -8h, US America/New_York）
- 保持 HK 符号补零逻辑不变

### 3.3 实验详情页对接

实验管理模块的详情 Drawer 中，权益图/持仓/交易数据改为调用管理平台自己的 Dashboard API：
```
/api/admin/dashboard/equity/{exp_id}?run_id=X
/api/admin/dashboard/experiments/{exp_id}/positions
/api/admin/dashboard/trades/{exp_id}?run_id=X
```

不再依赖 Dashboard (:8090)。

## 4. 前端页面

### 4.1 菜单结构调整

在 Ant Design Pro 菜单中新增 "Dashboard" 项（放在最前面），内部子 Tab：

```
Dashboard
├── Overview      — 所有类型实验汇总卡片
├── Live          — 仅 live_* 实验
├── Paper Run     — 仅 paper_* 实验 + Paper Run 回测
├── Prod          — 仅 prod_* 实验（只读）
├── Debug         — 仅 debug_* 实验
├── Pipeline      — 数据管道健康
└── Alerts        — 实时告警
```

### 4.2 各子页面功能

| 子 Tab | 功能 | 实现 |
|--------|------|------|
| **Overview** | 所有实验汇总卡片 + US/HK 行情图 | ProTable 卡片 + Plotly K 线 |
| **Live** | 实验选择器 + 权益图/回撤图 + 持仓表 + 交易表 + 指标卡片 | 与现有 Live Tab 功能一致 |
| **Paper Run** | Paper Run 列表 + 详情（指标卡、权益图、交易） | 与现有 Paper Run Tab 一致 |
| **Prod** | 与 Live 相同但只读 | 复用 Live 组件，隐藏操作按钮 |
| **Debug** | 与 Live 相同 | 复用 Live 组件 |
| **Pipeline** | 数据管道健康状态卡片 | BQ 各表最新时间戳 |
| **Alerts** | 占位提示 | 简单卡片 |

### 4.3 组件复用

Live / Prod / Debug 三个 Tab 复用同一个 `ExperimentDetail` React 组件，通过 props 区分 type 过滤。

### 4.4 K 线图

Dashboard 的 US/HK 行情图使用 Plotly.js。React 中用 `react-plotly.js` 或直接用 Plotly CDN。

## 5. 实验详情数据加载方案

### 问题

实验管理模块的详情 Drawer，需要展示权益图、持仓、交易数据。原来调 Dashboard (:8090) 的 API，Dashboard 挂了就看不到。

### 方案

管理平台后端直接实现这些 API（§3.2），不依赖外部服务：

```
管理平台前端 → /api/admin/dashboard/equity/{exp_id}
                  ↓
             admin/server.py → BigQuery (直接查)
```

**优势：**
- Dashboard 服务挂了不影响管理平台
- 管理平台是 SSOT
- 后续可以逐步下线 Dashboard (:8090)

## 6. 迁移计划

### Phase 1: 后端 API 迁移
1. 复制 `dashboard/server.py` 中的 API 函数到 `admin/server.py`
2. 统一前缀为 `/api/admin/dashboard/*`
3. 验证所有 API 正常返回

### Phase 2: 前端 Dashboard Tab
1. 创建 `Dashboard.tsx` 主组件（含子 Tab 切换）
2. 重写 Overview 页（实验卡片 + K 线图）
3. 重写 Live 页（复用实验详情组件）
4. 重写 Paper Run 页
5. 重写 Pipeline 页
6. Prod/Debug/Alerts 页（复用或简化）

### Phase 3: 实验详情对接
1. 更新实验管理模块的详情 Drawer
2. API 改为 `/api/admin/dashboard/*`

## 7. 与现有系统的关系

| 现有系统 | 整合后 |
|---------|--------|
| Dashboard (:8090) | 逐步下线。API 迁移到管理平台后，Dashboard 可停止 |
| 管理平台 (:8091) | 新增 Dashboard Tab |
| cloudflared | Dashboard tunnel 可关闭，只保留管理平台 tunnel |
| BQ | 不变，管理平台直查 |
