# 缓存子系统设计 & 实现计划

> 2026-06-10 | Jarvis | 状态: 待审阅

## 0. 动机

当前系统缓存是散装的 ad-hoc 实现：
- `admin_data_tables()` 用函数属性挂 24h TTL，无法复用
- `_load_symbols_config()` 每次都读磁盘 YAML
- Dashboard 全部 API 每次请求直打 BigQuery（一个页面可能触发 5-8 次 BQ 查询）
- 无统一失效机制、无命中率统计、无手动刷新

**目标：** 一个可拔插的缓存子系统，每个模块注册即用，统一管理 TTL、失效、统计、手动刷新。

---

## 1. 架构总览

```
┌──────────────────────────────────────────────────────────┐
│               CacheManager (单例, 注册中心)                │
│                                                          │
│  modules: dict[str, CacheModule]                         │
│  register(name, ttl, max_size, warmup_fn?)               │
│  unregister(name)                                        │
│  get(name) → CacheModule                                 │
│  invalidate(pattern) → int    # 支持 "dashboard:*"        │
│  stats() → dict               # 所有模块汇总              │
│  refresh(name, factory_args) → Any  # 失效+预热          │
└──────┬──────────────┬──────────────┬─────────────────────┘
       │              │              │
  ┌────▼────┐   ┌────▼────┐   ┌────▼────┐
  │CacheModule│ │CacheModule│ │CacheModule│  ... 可无限注册
  │dashboard │  │market    │  │factors   │
  │:exper.   │  │:bars:5m  │  │:list     │
  │ TTL=1h   │  │ TTL=5min │  │ TTL=7d   │
  └────┬────┘   └────┬────┘   └────┬────┘
       │              │              │
  ┌────▼────┐   ┌────▼────┐   ┌────▼────┐
  │TTLCache │   │TTLCache │   │TTLCache │
  │(backend)│   │(backend)│   │(backend)│
  └─────────┘   └─────────┘   └─────────┘
```

### 核心原则

1. **一个模块 = 一个 TTL** — 相同 TTL 和失效策略的缓存归为一个模块
2. **模块内按 key 细分** — 如 `market:bars:5m` 模块，key = `"us:AAPL,limit=78"`
3. **不可变** — 注册后模块名称/TTL 不变（删除模块再重新注册例外）
4. **refresh = invalidate + warm** — 清缓存后立即调用 factory 回热，返回新数据

---

## 2. 缓存模块清单

### 分组设计

共 17 个缓存模块，按数据更新频率分三组：

#### 组 A: 实验数据 (TTL = 1h)

| 模块名 | 缓存内容 | 涉及 API | Key 粒度 |
|--------|---------|---------|---------|
| `dashboard:experiments` | 实验概览列表 + meta | `dash_experiments` + `dash_experiments_meta` | 按 type 分 key: `list:live`, `list:paper`, `meta` |
| `dashboard:equity` | 权益曲线 | `dash_equity_series` | `{exp_id}:{run_id}` |
| `dashboard:trades` | 交易记录 | `dash_trades` | `{exp_id}:{run_id}:{limit}` |
| `dashboard:positions` | 当前持仓 | `dash_experiment_positions` | `{exp_id}:{run_id}` |
| `dashboard:paper_runs` | Paper Run 列表 | `dash_paper_runs` | `list:{limit}` |
| `dashboard:paper_run_detail` | Paper Run 详情 | `dash_paper_run_detail` | `{run_id}` |
| `dashboard:experiment_runs` | 实验运行历史 | `dash_experiment_runs` | `{exp_id}` |

#### 组 B: 行情 & 实时数据 (分短/长 TTL)

| 模块名 | TTL | 缓存内容 | 涉及 API | Key 粒度 |
|--------|-----|---------|---------|---------|
| `market:bars:5m` | **5min** | 5分钟K线 | `dash_market_bars(freq=5m)` | `{market}:{symbol}:{limit}` |
| `market:bars:1d` | **24h** | 日K线 | `dash_market_bars(freq=1d)` | `{market}:{symbol}:{limit}` |
| `dashboard:pipeline` | **1h** | 数据新鲜度 | `dash_pipeline` | `pipeline` (单 key) |

#### 组 C: 低频配置/元数据 (TTL = 7d)

| 模块名 | 缓存内容 | 涉及 API | Key 粒度 |
|--------|---------|---------|---------|
| `data:tables` | BQ 表列表 | `admin_data_tables` | `tables` (单 key) |
| `market:symbols` | 标的列表 | `dash_market_symbols` | `{market}` |
| `factors:list` | 因子列表 + 覆盖数据 | `admin_factors` | `factors` (单 key) |
| `models:list` | 模型列表 | `admin_models` | `models` (单 key) |
| `models:versions` | 模型版本信息 | `admin_model_versions` + `admin_model_history` | `{name}` |
| `cron:list` | Cron 任务列表 | `admin_cron_list` | `cron` (单 key) |
| `strategies:list` | 策略文件列表 | `admin_strategies` + `admin_strategy_read` | `list`, `content:{name}` |

> **注意：** ML datasets / configs / center 和 experiment configs 目前接口调用量极小，暂不缓存。需要时再注册即可。

---

## 3. 手动刷新设计

### 3.1 后端 API

新增 3 个管理端点（共 ~80 行）：

| 方法 | 路径 | 功能 |
|------|------|------|
| `GET` | `/api/admin/cache/modules` | 列出所有模块（name, ttl, size, hits, misses, hit_rate） |
| `POST` | `/api/admin/cache/invalidate` | 失效指定模块的缓存 |
| `POST` | `/api/admin/cache/refresh` | 失效 + 回热，直接返回新数据 |

#### `POST /api/admin/cache/invalidate`

```json
// Request
{
  "module": "market:bars:5m",   // 精确模块名，或 "*" 全部，或 "dashboard:*"
  "key": null                    // 可选：失效特定 key；null = 清空整个模块
}

// Response
{
  "invalidated": 34,
  "modules_affected": ["market:bars:5m"]
}
```

#### `POST /api/admin/cache/refresh` ⭐ 关键

```json
// Request
{
  "module": "dashboard:experiments",
  "params": { "type": "live" }  // 传给 warmup 的参数
}

// Response — 直接就是预热后的新数据
{
  "module": "dashboard:experiments",
  "from_cache": false,
  "data": [...],                // 和原始 API 返回格式一致
  "took_ms": 320
}
```

**实现方式：** 每个 CacheModule 注册时可选附带 `warmup_fn`，它是原始数据查询函数。refresh 时：
1. 失效旧缓存
2. 调用 `warmup_fn(**params)` 生成新数据
3. 回填到缓存
4. 返回新数据

#### `GET /api/admin/cache/modules`

```json
{
  "modules": [
    {
      "name": "dashboard:experiments",
      "ttl": 3600,
      "max_size": 200,
      "current_size": 5,
      "hits": 142,
      "misses": 18,
      "hit_rate": 0.887
    },
    ...
  ]
}
```

### 3.2 前端交互

#### 方式一：缓存管理页面（全局视角）

Admin 左侧菜单新增"缓存管理"入口，页面内容：

```
┌──────────────────────────────────────────────────────────────────┐
│  📦 缓存管理                                     [全部失效] [全部刷新] │
├────────────────────────┬─────────┬──────┬────────┬────────┬──────┤
│ 模块                    │ TTL     │ 条目 │ 命中率  │ 状态   │ 操作  │
├────────────────────────┼─────────┼──────┼────────┼────────┼──────┤
│ dashboard:experiments  │ 1h      │ 5    │ 87%    │ ✅     │ 🔄   │
│ dashboard:equity       │ 1h      │ 3    │ 92%    │ ✅     │ 🔄   │
│ market:bars:5m         │ 5min    │ 34   │ 94%    │ ✅     │ 🔄   │
│ market:bars:1d         │ 24h     │ 12   │ 98%    │ ✅     │ 🔄   │
│ dashboard:pipeline     │ 1h      │ 1    │ 100%   │ ✅     │ 🔄   │
│ data:tables            │ 7d      │ 1    │ 100%   │ ✅     │ 🔄   │
│ factors:list           │ 7d      │ 1    │ 100%   │ ✅     │ 🔄   │
│ ...                    │         │      │        │        │      │
└────────────────────────┴─────────┴──────┴────────┴────────┴──────┘
```

每行一个 🔄 按钮，点击后失效该模块缓存。模块自己重新预热。

#### 方式二：数据面板内嵌刷新（高频操作）

在每个数据面板右上角加 🔄 小按钮：

```
Dashboard → Overview / Live / ...
  ┌──────────────────────────────────┐
  │  实验列表                    🔄   │  ← 失效 dashboard:experiments
  │  ┌────────────────────────────┐  │
  │  │  Exp1  live  running  ... │  │
  │  └────────────────────────────┘  │
  └──────────────────────────────────┘

  行情中心
  ┌──────────────────────────────────┐
  │  AAPL K线                    🔄   │  ← 调用 cache/refresh 直接返回新数据
  │  ┌────────────────────────────┐  │     + 更新图表
  │  │      📈  chart             │  │
  │  └────────────────────────────┘  │
  └──────────────────────────────────┘
```

**前端刷新流程（两步）：**

```typescript
const handleRefresh = async () => {
  setLoading(true)
  // Step 1: 失效缓存 + 回热（或直接调 refresh API）
  const refreshRes = await fetch('/api/admin/cache/refresh', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ 
      module: cacheModuleName,    // 如 "dashboard:experiments"
      params: refreshParams        // 如 { type: "live" }
    })
  })
  const { data } = await refreshRes.json()
  
  // Step 2: 用新数据更新 UI
  setData(data)
  setLoading(false)
}
```

> 如果某个模块没有注册 `warmup_fn`（如低频 7d 模块），点刷新就只做 invalidate，然后前端重新调原始 API 获取数据。

---

## 4. 自动失效触发器

除了手动刷新，系统事件也会自动失效相关缓存，避免返回过期数据：

| 事件 | 失效范围 |
|------|---------|
| 实验启动 / 停止 / 删除 | `dashboard:experiments`, `dashboard:equity:*`, `dashboard:trades:*`, `dashboard:positions:*` |
| 新 5m bar 入库（ws_collector 回调） | `dashboard:pipeline`, `market:bars:5m:*` |
| 日线聚合完成（compute_daily_bars） | `market:bars:1d:*`, `dashboard:pipeline` |
| 回填完成 | `market:bars:*:*`, `dashboard:pipeline` |
| Cron 增删改 | `cron:list` |
| 因子注册/注销 | `factors:list` |
| 模型训练完成 | `models:list`, `models:versions:{name}` |
| 策略保存/删除 | `strategies:list` |
| BQ 表结构变更 | `data:tables` |
| symbols.yaml 修改 | `market:symbols:*` |

> **实现方式：** 在各业务逻辑的 success 分支末尾加一行 `cache_mgr.invalidate("xxx")`。不侵入核心逻辑，只是尾部通知。

---

## 5. 依赖

| 依赖 | 版本 | 用途 | 备注 |
|------|------|------|------|
| `cachetools` | ≥5.3 (已安装 7.1.4) | TTLCache 内存存储 | 纯 Python，零额外依赖 |

不引入 Redis/外部存储。后续如需多 worker 共享，可加 Redis backend 而不改接口。

---

## 6. 文件结构

```
common/
  cache_subsystem/
    __init__.py       # 公开: CacheManager, CacheModule, get_cache_manager()
    manager.py        # CacheManager: register/unregister/get/invalidate/refresh/stats
    module.py         # CacheModule: get/set/get_or_compute/invalidate/stats
    backends.py       # MemoryBackend (cachetools.TTLCache), 预留 RedisBackend
```

所有新代码在 `common/cache_subsystem/`，~300 行。

```
admin/
  server.py           # 改动: lifespan 注册 17 个模块 + 3 个管理 API + 改造 ~12 个 API 使用缓存
  frontend/
    src/
      pages/
        CacheManager.tsx   # 新页面: 缓存管理
      components/
        CacheRefresh.tsx   # 可复用刷新按钮组件
      api.ts               # 加 cache API 调用
      App.tsx              # 加路由: /cache
      ...
```

---

## 7. 实现步骤

| # | 步骤 | 内容 | 行数 | 可验证 |
|---|------|------|------|--------|
| 1 | **核心库** | `common/cache_subsystem/` 三个文件 | ~300 | `python -c "from common.cache_subsystem import *"` |
| 2 | **注册模块** | `server.py` lifespan 中注册 17 个模块 | ~50 | 启动后 `GET /api/admin/cache/modules` 返回 17 条 |
| 3 | **管理 API** | `/api/admin/cache/modules|invalidate|refresh` | ~80 | curl 手动调用验证 |
| 4 | **改造 Dashboard API** | 12 个 dash_xxx 函数外层包缓存 | ~150 | 两次连续请求，第二次 BQ 日志中无对应查询 |
| 5 | **改造低频 API** | factors/models/cron/strategies/data 外层包缓存 | ~100 | 同步骤 4 |
| 6 | **自动失效钩子** | 业务逻辑尾部加 `cache_mgr.invalidate` | ~50 | 触发事件后检查缓存是否被清空 |
| 7 | **前端缓存管理页** | `CacheManager.tsx` + 路由 | ~200 | 页面上看到所有模块和命中率 |
| 8 | **前端刷新按钮** | 各数据面板加 `CacheRefresh` 组件 | ~150 | 点击刷新按钮后数据更新 |
| 9 | **清理旧缓存代码** | 移除 `admin_data_tables` 的 ad-hoc cache + `_load_symbols_config` 改造 | ~20 | 旧模式代码不再存在 |
| 10 | **端到端验证** | Dashboard 完整走一遍，确认缓存命中 + 手动刷新有效 | - | BQ console 查询数明显减少 |

---

## 8. 遗漏检查

逐一核对现有 83 个 API 端点，确认没有遗漏缓存候选：

| API 端点 | 方法 | 缓存? | 说明 |
|----------|------|-------|------|
| `/api/admin/health` | GET | ❌ | health check，不缓存 |
| `/api/admin/tasks` | GET | ❌ | 任务队列状态需要实时 |
| `/api/admin/tasks` | POST | ❌ | 创建任务 |
| `/api/admin/tasks/{id}` | GET | ❌ | 单任务状态实时 |
| `/api/admin/experiments` | GET | ❌ (暂) | ~~可缓存~~ 当前调用量小，先不加 |
| `/api/admin/experiments/runs/{exp_id}` | GET | ❌ | 和 dashboard 共享数据源，dashboard 已有缓存 |
| `/api/admin/experiments/register` | POST | ❌ | 写操作 |
| `/api/admin/experiments/create-from-config` | POST | ❌ | 写操作 |
| `/api/admin/experiments/configs` | GET | ❌ (暂) | 低频，需要时注册 |
| `/api/admin/experiments/{exp_id}/*` | */POST/PUT/DELETE | ❌ | 写操作 + 触发自动失效 |
| `/api/admin/data/f10` | GET | ❌ (暂) | F10 数据用不到，暂不缓存 |
| `/api/admin/data/tables` | GET | ✅ | 7d, `data:tables` |
| `/api/admin/data/collectors` | GET | ❌ | 实时状态，不缓存 |
| `/api/admin/data/backfill*` | GET/POST | ❌ | 回填操作，不缓存 |
| `/api/admin/logs/*` | GET/WS | ❌ | 日志 WebSocket 实时流 |
| `/api/admin/cron` | GET | ✅ | 7d, `cron:list` |
| `/api/admin/cron/*` | POST/PUT | ❌ | 写操作 + 触发 `cron:list` 失效 |
| `/api/admin/models` | GET | ✅ | 7d, `models:list` |
| `/api/admin/models/{name}/history` | GET | ✅ | 7d, `models:versions` |
| `/api/admin/models/{name}/versions` | GET | ✅ | 7d, `models:versions` |
| `/api/admin/models/train` | POST | ❌ | 写操作 + 触发 `models:list` 失效 |
| `/api/admin/models/{name}/stage` | POST | ❌ | 写操作 + 触发 `models:versions` 失效 |
| `/api/admin/models/{name}/versions/{v}` | DELETE | ❌ | 写操作 + 触发失效 |
| `/api/admin/strategies` | GET | ✅ | 7d, `strategies:list` |
| `/api/admin/strategies/{name}` | GET | ✅ | 7d, `strategies:list` keyed |
| `/api/admin/strategies/{name}` | PUT/DELETE | ❌ | 写操作 + 触发失效 |
| `/api/admin/factors` | GET | ✅ | 7d, `factors:list` |
| `/api/admin/factors/{id}/toggle` | POST | ❌ | 写操作 + 触发因子失效 |
| `/api/admin/factors/{id}/evaluate` | POST | ❌ | 触发计算 |
| `/api/admin/factors/compute` | POST | ❌ | 触发批量计算 |
| `/api/admin/ml/datasets` | GET | ❌ (暂) | 低频，需要时加模块 |
| `/api/admin/ml/datasets/{market}/factors` | GET | ❌ (暂) | 低频 |
| `/api/admin/ml/datasets` | POST | ❌ | 写操作 |
| `/api/admin/ml/datasets/{id}/*` | POST/DELETE | ❌ | 写操作 |
| `/api/admin/ml/configs` | GET | ❌ (暂) | 低频 |
| `/api/admin/ml/configs/{name}` | GET | ❌ (暂) | 低频 |
| `/api/admin/ml/configs/{name}` | PUT/DELETE | ❌ | 写操作 |
| `/api/admin/ml/center` | GET | ❌ (暂) | 低频，MLflow proxy 数据 |
| `/api/admin/ml/center/{name}` | DELETE | ❌ | 写操作 |
| `/api/admin/mlflow/*` | ALL | ❌ | MLflow 反向代理，不缓存 |
| `/api/admin/dashboard/experiments` | GET | ✅ | 1h, `dashboard:experiments` |
| `/api/admin/dashboard/experiments/meta` | GET | ✅ | 1h, `dashboard:experiments` |
| `/api/admin/dashboard/equity/{exp_id}` | GET | ✅ | 1h, `dashboard:equity` |
| `/api/admin/dashboard/trades/{exp_id}` | GET | ✅ | 1h, `dashboard:trades` |
| `/api/admin/dashboard/experiments/{exp_id}/positions` | GET | ✅ | 1h, `dashboard:positions` |
| `/api/admin/dashboard/experiments/{exp_id}/runs` | GET | ✅ | 1h, `dashboard:experiment_runs` |
| `/api/admin/dashboard/paper-runs` | GET | ✅ | 1h, `dashboard:paper_runs` |
| `/api/admin/dashboard/paper-runs/{run_id}` | GET | ✅ | 1h, `dashboard:paper_run_detail` |
| `/api/admin/dashboard/pipeline` | GET | ✅ | 1h, `dashboard:pipeline` |
| `/api/admin/dashboard/market/symbols/{market}` | GET | ✅ | 7d, `market:symbols` |
| `/api/admin/dashboard/market/{market}/{symbol}` | GET | ✅ | 5min/24h, `market:bars:{freq}` |

**检查结论：** 83 个端点中，17 个缓存模块覆盖了所有需要缓存的读操作。ML/experiment configs 等低频接口暂不缓存，需要时一行注册即可。Collector 状态、日志 WebSocket、写操作类 API 不缓存（设计如此）。

---

## 9. 潜在风险 & 注意事项

1. **warmup_fn 参数签名** — 各 API 的查询参数不同（有的用 Query param，有的用 path param），warmup_fn 需要统一为 `**kwargs` 或明确签名。设计为：每个模块注册时声明 `param_names: list[str]`，refresh 时从 body.params 映射过去。

2. **缓存 key 设计** — 需要足够区分不同请求。如 `market:bars:5m` 的 key = `"us:AAPL:78"`（market:symbol:limit），要包含所有影响结果差异的参数。

3. **BQ 查询 size 影响** — 大查询（如全历史权益曲线）缓存收益最大，但要确保 `max_size` 够用。`dashboard:equity` 设置 `max_size=200`（够存 50+ 实验 × 多个 run）。

4. **CacheManager 单例线程安全** — FastAPI 是 async + 多 worker。`cachetools.TTLCache` 本身不是线程安全的。需要加 `threading.RLock` 或在 CacheModule 层面加锁。**计划用 `@synchronized` 装饰器或在 `get_or_compute` 方法内加锁。**

5. **Worker 进程间不共享** — uvicorn 多 worker 模式下每个 worker 有独立的缓存实例。首次请求可能 miss。这是可接受的折衷（避免引入 Redis 复杂度），后续可选升级。

6. **缓存预热** — refresh 调用 `warmup_fn` 时如果 BQ 查询慢（2-5s），前端需要显示 loading 状态。已在设计中考虑。

---

## 10. 待确认

- [ ] 模块划分（17 个）是否合适？有没有需要合并或拆分的？
- [ ] TTL 值是否需要调整？（1h / 5min / 24h / 7d）
- [ ] `dashboard:equity/trades/positions/paper_run_detail` 的 `max_size` 设置多少？（建议 200）
- [ ] 前端：先做"缓存管理页面"，再逐步给各面板加刷新按钮？还是一起做？
- [ ] 自动失效钩子：是否全部实现，还是先只做手动刷新，后续再加自动失效？
