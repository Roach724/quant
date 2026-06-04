# Quant Admin Platform — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a React + FastAPI admin platform to manage experiments, data, logs, cron, models, and factors through a unified web UI.

**Architecture:** React 18 + Vite + Ant Design Pro frontend → FastAPI :8091 backend with SQLAlchemy/SQLite → worker process consuming task queue → subprocess calls to existing Python modules.

**Tech Stack:** React 18, Vite 5, TypeScript, Ant Design Pro 5, FastAPI, SQLAlchemy 2, SQLite

**Spec:** `docs/superpowers/specs/2026-06-04-admin-platform-design.md`

---

## Phase 0: Project Scaffold

### Task 0.1: Create React frontend project

**Files:**
- Create: `admin/frontend/` (Vite + React + TS + Ant Design Pro)

- [ ] **Step 1: Scaffold Vite project**

```bash
cd /opt/quant-dev/admin
npm create vite@latest frontend -- --template react-ts
cd frontend
npm install antd @ant-design/pro-layout @ant-design/pro-table react-router-dom @ant-design/icons
npm install axios dayjs
```

- [ ] **Step 2: Create basic layout**

Create `admin/frontend/src/App.tsx`:

```tsx
import { ProLayout, PageContainer } from '@ant-design/pro-layout';
import { DashboardOutlined, CloudServerOutlined, FileTextOutlined,
         ClockCircleOutlined, ExperimentOutlined, FunctionOutlined } from '@ant-design/icons';
import { useState } from 'react';
import { BrowserRouter, useNavigate, useLocation } from 'react-router-dom';

const menuData = [
  { path: '/experiments', name: '实验管理', icon: <ExperimentOutlined /> },
  { path: '/data', name: '数据采集', icon: <CloudServerOutlined /> },
  { path: '/logs', name: '日志浏览', icon: <FileTextOutlined /> },
  { path: '/cron', name: 'Cron 任务', icon: <ClockCircleOutlined /> },
  { path: '/models', name: '模型 & 策略', icon: <DashboardOutlined /> },
  { path: '/factors', name: '因子管理', icon: <FunctionOutlined /> },
];

export default function App() {
  const [pathname, setPathname] = useState('/experiments');
  return (
    <BrowserRouter>
      <ProLayout
        title="Quant Admin"
        logo={null}
        menuDataRender={() => menuData}
        location={{ pathname }}
        onMenuHeaderClick={() => setPathname('/experiments')}
        menuItemRender={(item, dom) => (
          <a onClick={() => setPathname(item.path || '/')}>{dom}</a>
        )}
      >
        <PageContainer>
          {pathname === '/experiments' && <div>Experiments</div>}
          {pathname === '/data' && <div>Data</div>}
          {pathname === '/logs' && <div>Logs</div>}
          {pathname === '/cron' && <div>Cron</div>}
          {pathname === '/models' && <div>Models</div>}
          {pathname === '/factors' && <div>Factors</div>}
        </PageContainer>
      </ProLayout>
    </BrowserRouter>
  );
}
```

- [ ] **Step 3: Verify dev server starts**

```bash
cd /opt/quant-dev/admin/frontend && npm run dev
```

Expected: Browser opens at localhost:5173 with sidebar menu.

- [ ] **Step 4: Commit**

```bash
cd /opt/quant-dev && git add admin/ && git commit -m "feat: admin frontend scaffold — React + Ant Design Pro"
```

### Task 0.2: Create FastAPI backend scaffold

**Files:**
- Create: `admin/__init__.py`
- Create: `admin/server.py`
- Create: `admin/models.py`
- Create: `admin/worker.py`

- [ ] **Step 1: Create SQLite models**

`admin/models.py`:

```python
"""SQLAlchemy models for admin platform."""
from datetime import datetime
from sqlalchemy import create_engine, Column, Integer, String, Text, DateTime, JSON
from sqlalchemy.orm import DeclarativeBase, Session

DB_PATH = "/var/quant/admin.db"

engine = create_engine(f"sqlite:///{DB_PATH}", echo=False)


class Base(DeclarativeBase):
    pass


class Task(Base):
    __tablename__ = "tasks"

    id = Column(Integer, primary_key=True, autoincrement=True)
    type = Column(String(50), nullable=False, index=True)
    status = Column(String(20), nullable=False, default="pending", index=True)
    params = Column(JSON, nullable=True)
    result = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    started_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)


def init_db():
    Base.metadata.create_all(engine)


def get_session() -> Session:
    return Session(engine)
```

- [ ] **Step 2: Create FastAPI server skeleton**

`admin/server.py`:

```python
"""Admin platform — FastAPI server."""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from admin.models import init_db, get_session, Task
from datetime import datetime

app = FastAPI(title="Quant Admin")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def startup():
    init_db()


@app.get("/api/health")
def health():
    return {"status": "ok", "ts": datetime.utcnow().isoformat()}


@app.get("/api/tasks")
def list_tasks(status: str = "", limit: int = 50):
    session = get_session()
    q = session.query(Task).order_by(Task.created_at.desc())
    if status:
        q = q.filter(Task.status == status)
    tasks = q.limit(limit).all()
    return [{"id": t.id, "type": t.type, "status": t.status,
             "created_at": t.created_at.isoformat() if t.created_at else None,
             "result": t.result} for t in tasks]


@app.post("/api/tasks")
def create_task(type: str, params: dict = None):
    session = get_session()
    task = Task(type=type, params=params or {}, status="pending")
    session.add(task)
    session.commit()
    return {"id": task.id, "status": task.status}
```

- [ ] **Step 3: Create worker skeleton**

`admin/worker.py`:

```python
"""Admin platform — background task worker."""
import time
import subprocess
import logging
from admin.models import init_db, get_session, Task

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("admin.worker")

PROJECT_ROOT = "/opt/quant-prod"
PYTHON = f"{PROJECT_ROOT}/.venv/bin/python3"


def execute_task(task: Task):
    """Execute a single task based on its type."""
    logger.info("Executing task %d: type=%s", task.id, task.type)
    try:
        if task.type == "shell":
            cmd = task.params.get("cmd", "")
            result = subprocess.run(
                cmd, shell=True, cwd=PROJECT_ROOT,
                capture_output=True, text=True, timeout=300,
            )
            task.result = result.stdout[-2000:] or result.stderr[-2000:]
            task.status = "done" if result.returncode == 0 else "failed"
        else:
            task.result = f"Unknown task type: {task.type}"
            task.status = "failed"
    except Exception as e:
        task.result = str(e)
        task.status = "failed"


def main():
    init_db()
    logger.info("Worker started")
    while True:
        session = get_session()
        try:
            task = session.query(Task).filter(
                Task.status == "pending"
            ).order_by(Task.created_at).first()
            if task:
                task.status = "running"
                task.started_at = __import__("datetime").datetime.utcnow()
                session.commit()
                execute_task(task)
                task.finished_at = __import__("datetime").datetime.utcnow()
                session.commit()
        except Exception as e:
            logger.exception("Worker loop error")
            session.rollback()
        finally:
            session.close()
        time.sleep(2)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Verify backend starts**

```bash
cd /opt/quant-dev && PYTHONPATH=/opt/quant-dev .venv/bin/python3 -m uvicorn admin.server:app --port 8092 &
sleep 2 && curl -s http://localhost:8092/api/health
# Expected: {"status":"ok","ts":"..."}
kill %1
```

- [ ] **Step 5: Test task queue end-to-end**

```bash
cd /opt/quant-dev && PYTHONPATH=/opt/quant-dev .venv/bin/python3 admin/worker.py &
WORKER_PID=$!
sleep 1

# Create a task
curl -s -X POST "http://localhost:8092/api/tasks?type=shell" \
  -H "Content-Type: application/json" \
  -d '{"cmd": "echo hello world"}'

sleep 3
# Check task completed
curl -s "http://localhost:8092/api/tasks" | python3 -m json.tool

kill $WORKER_PID
```

Expected: Task status changes from pending → done, result contains "hello world".

- [ ] **Step 6: Commit**

```bash
cd /opt/quant-dev && git add admin/ && git commit -m "feat: admin backend scaffold — FastAPI + SQLite + worker"
```

### Task 0.3: Wire frontend to backend API

**Files:**
- Modify: `admin/frontend/src/App.tsx`

- [ ] **Step 1: Add API service layer**

Create `admin/frontend/src/api.ts`:

```typescript
import axios from 'axios';

const API_BASE = 'http://localhost:8092';

export const api = {
  get: (path: string) => axios.get(`${API_BASE}${path}`).then(r => r.data),
  post: (path: string, data?: any) => axios.post(`${API_BASE}${path}`, data).then(r => r.data),
};

// Task API
export const createTask = (type: string, cmd: string) =>
  api.post(`/api/tasks?type=${type}`, { cmd });

export const listTasks = (status = '') =>
  api.get(`/api/tasks?status=${status}&limit=20`);
```

- [ ] **Step 2: Verify connectivity**

Run `npm run dev`, open browser devtools, call `api.get('/api/health')` in console → should return `{status: "ok"}`.

- [ ] **Step 3: Commit**

```bash
cd /opt/quant-dev && git add admin/frontend/src/api.ts && git commit -m "feat: frontend API service layer"
```

---

## Phase 1: Experiment Management

### Task 1.1: Experiment list page

**Files:**
- Create: `admin/frontend/src/pages/Experiments.tsx`

- [ ] **Step 1: Build experiment list component**

```tsx
import { ProTable } from '@ant-design/pro-table';
import { Button, Tag, Space, message, Popconfirm } from 'antd';
import { PlayCircleOutlined, PauseCircleOutlined, ReloadOutlined, StopOutlined } from '@ant-design/icons';
import { api } from '../api';
import { useState } from 'react';

const statusColor: Record<string, string> = {
  running: 'green', paused: 'blue', completed: 'default',
  archived: 'default', pending: 'orange', failed: 'red',
};

export default function Experiments() {
  const [loading, setLoading] = useState(false);

  const columns = [
    { title: 'ID', dataIndex: 'exp_id', key: 'exp_id', width: 200 },
    { title: '名称', dataIndex: 'name', key: 'name' },
    { title: '类型', dataIndex: 'type', key: 'type', width: 80 },
    { title: '市场', dataIndex: 'market', key: 'market', width: 60 },
    { title: '版本', dataIndex: 'version', key: 'version', width: 60 },
    { title: '状态', dataIndex: 'status', key: 'status', width: 100,
      render: (_: any, r: any) => <Tag color={statusColor[r.status] || 'default'}>{r.status}</Tag> },
    { title: '当前 Run', dataIndex: 'current_run', key: 'current_run', width: 200, ellipsis: true },
    { title: '操作', key: 'actions', width: 200,
      render: (_: any, r: any) => (
        <Space>
          {r.status !== 'running' && (
            <Button size="small" icon={<PlayCircleOutlined />} type="primary"
              onClick={() => handleAction('start', r.exp_id)}>启动</Button>
          )}
          {r.status === 'running' && (
            <Button size="small" icon={<PauseCircleOutlined />}
              onClick={() => handleAction('stop', r.exp_id)}>停止</Button>
          )}
          <Button size="small" icon={<ReloadOutlined />}
            onClick={() => handleAction('restart', r.exp_id)}>重启</Button>
        </Space>
      ),
    },
  ];

  const handleAction = async (action: string, expId: string) => {
    setLoading(true);
    try {
      await api.post(`/api/experiments/${expId}/${action}`);
      message.success(`${action} ${expId}`);
      // Refresh
    } catch (e: any) {
      message.error(e.message);
    }
    setLoading(false);
  };

  return (
    <ProTable
      columns={columns}
      request={async () => {
        const data = await api.get('/api/experiments/meta');
        return { data, success: true };
      }}
      rowKey="exp_id"
      search={false}
      loading={loading}
      headerTitle="实验管理"
      toolBarRender={() => [
        <Button key="register" type="primary">注册实验</Button>,
      ]}
    />
  );
}
```

- [ ] **Step 2: Add backend experiment endpoints**

In `admin/server.py`, add:


## Phase 2: Data Collection + Data Map

### Task 2.1: Backend — BQ table browser + collector status

**Files:**
- Modify: `admin/server.py`

- [ ] **Step 1: Add data map endpoint**

```python
from google.cloud import bigquery
import os, glob

@app.get("/api/admin/data/tables")
def admin_data_tables():
    """Return all BQ tables with row counts, schemas, last write times."""
    client = bigquery.Client(project="deductive-notch-495015-c2")
    query = """
        SELECT table_name, creation_time
        FROM quant.INFORMATION_SCHEMA.TABLES
        ORDER BY table_name
    """
    rows = client.query(query).result()
    tables = []
    for r in rows:
        name = r.table_name
        try:
            cnt = list(client.query(f"SELECT COUNT(*) AS cnt FROM quant.{name}").result())[0].cnt
        except Exception:
            cnt = 0
        try:
            ts = list(client.query(f"SELECT MAX(timestamp) AS latest FROM quant.{name}").result())[0].latest
            latest_str = ts.isoformat() if ts else None
        except Exception:
            latest_str = None
        try:
            cols = client.query(
                f"SELECT column_name, data_type FROM quant.INFORMATION_SCHEMA.COLUMNS "
                f"WHERE table_name='{name}' ORDER BY ordinal_position"
            ).result()
            schema_cols = [{"name": c.column_name, "type": c.data_type} for c in cols]
        except Exception:
            schema_cols = []
        tables.append({
            "table_name": name, "row_count": cnt,
            "last_write": latest_str, "schema": schema_cols,
        })
    return tables


@app.get("/api/admin/data/collectors")
def admin_data_collectors():
    """ws_collector status + last heartbeat."""
    import subprocess
    try:
        r = subprocess.run(["systemctl", "is-active", "ws-collector"], capture_output=True, text=True, timeout=5)
        status = r.stdout.strip()
    except Exception:
        status = "unknown"
    heartbeat = None
    try:
        with open("/var/log/quant/prod/collector/ws_collector.log") as f:
            for line in reversed(list(f.readlines()[-50:])):
                if "HEARTBEAT" in line:
                    heartbeat = json.loads(line).get("ts")
                    break
    except Exception:
        pass
    return {"ws_collector": status, "last_heartbeat": heartbeat}
```

- [ ] **Step 2: DataMap frontend page**

Create `admin/frontend/src/pages/DataMap.tsx` with ProTable: columns = table_name, row_count, last_write, and a "Schema" button that opens a Drawer showing column name + type. Also add a collector status card with start/stop buttons and heartbeat display.

- [ ] **Step 3: Commit** `git add -A && git commit -m "feat: data map + collector status"`

---

## Phase 3: Log Browser

### Task 3.1: Backend + Frontend

**Files:**
- Modify: `admin/server.py`
- Create: `admin/frontend/src/pages/LogViewer.tsx`

- [ ] **Step 1: Log API endpoints**

```python
import os, glob, json as _json
from fastapi import Query, WebSocket

LOG_ROOT = "/var/log/quant/prod"
LOG_MODULES = ["collector", "live", "factor", "cron", "train", "loader", "backfill", "quality", "adhoc"]

@app.get("/api/admin/logs/modules")
def admin_log_modules():
    modules = []
    for mod in LOG_MODULES:
        path = os.path.join(LOG_ROOT, mod)
        if os.path.isdir(path):
            files = glob.glob(os.path.join(path, "*.log"))
            modules.append({"name": mod, "file_count": len(files)})
    return modules

@app.get("/api/admin/logs")
def admin_logs(module: str = Query("collector"), level: str = Query(""),
               search: str = Query(""), lines: int = Query(100)):
    log_dir = os.path.join(LOG_ROOT, module)
    if not os.path.isdir(log_dir):
        return {"error": f"Unknown module: {module}", "lines": []}
    files = sorted(glob.glob(os.path.join(log_dir, "*.log")), reverse=True)
    if not files:
        return {"module": module, "lines": [], "file": None}
    log_file = files[0]
    result_lines = []
    with open(log_file) as f:
        all_lines = f.readlines()
        for line in reversed(all_lines[-max(lines * 3, 1000):]):
            if len(result_lines) >= lines:
                break
            try:
                entry = _json.loads(line)
                lvl = entry.get("level", "")
                msg = entry.get("msg", "")
                ts = entry.get("ts", "")
            except Exception:
                lvl = ""; msg = line.strip(); ts = ""
            if level and level.upper() != lvl.upper():
                continue
            if search and search.lower() not in msg.lower():
                continue
            result_lines.append({"ts": ts, "level": lvl, "msg": msg})
    result_lines.reverse()
    return {"module": module, "file": os.path.basename(log_file), "lines": result_lines}


@app.websocket("/ws/logs")
async def ws_logs(websocket: WebSocket, module: str = "collector"):
    await websocket.accept()
    log_dir = os.path.join(LOG_ROOT, module)
    files = sorted(glob.glob(os.path.join(log_dir, "*.log")), reverse=True)
    if not files:
        await websocket.close()
        return
    log_file = files[0]
    import asyncio
    try:
        with open(log_file) as f:
            f.seek(0, 2)
            while True:
                line = f.readline()
                if line:
                    try:
                        entry = _json.loads(line)
                        await websocket.send_json({"ts": entry.get("ts",""), "level": entry.get("level",""), "msg": entry.get("msg","")})
                    except Exception:
                        await websocket.send_json({"ts": "", "level": "", "msg": line.strip()})
                else:
                    await asyncio.sleep(0.5)
    except Exception:
        await websocket.close()
```

- [ ] **Step 2: LogViewer frontend**: Module dropdown, level filter, search input, live tail toggle (WebSocket), scrollable dark terminal-style log viewer.

- [ ] **Step 3: Commit** `git add -A && git commit -m "feat: log browser — JSON reader + real-time WebSocket tail"`

---

## Phase 4: Cron Management

### Task 4.1: Backend + Frontend

**Files:**
- Modify: `admin/server.py`
- Create: `admin/frontend/src/pages/CronJobs.tsx`

- [ ] **Step 1: Crontab endpoints**

```python
import subprocess

@app.get("/api/admin/cron")
def admin_cron_list():
    r = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
    lines = r.stdout.strip().split("\n") if r.stdout.strip() else []
    jobs = []
    for i, line in enumerate(lines):
        line = line.strip()
        if not line or line.startswith("#"):
            if line.startswith("#"):
                jobs.append({"index": i, "raw": line, "enabled": False, "schedule": "", "command": "", "comment": line.lstrip("# ")})
            continue
        parts = line.split(None, 5)
        if len(parts) >= 6:
            jobs.append({"index": i, "raw": line, "enabled": True, "schedule": " ".join(parts[:5]), "command": parts[5], "comment": ""})
    return jobs

@app.post("/api/admin/cron")
def admin_cron_save(jobs: list[dict]):
    lines = []
    for j in jobs:
        if j.get("raw"):
            lines.append(j["raw"])
        elif j.get("enabled"):
            lines.append(f"{j['schedule']} {j['command']}")
    crontab_content = "\n".join(lines) + "\n"
    proc = subprocess.run(["crontab", "-"], input=crontab_content, capture_output=True, text=True)
    if proc.returncode != 0:
        return {"error": proc.stderr}, 400
    return {"status": "ok"}

@app.post("/api/admin/cron/run")
def admin_cron_run(command: str = ""):
    session = get_session()
    task = Task(type="shell", params={"cmd": command}, status="pending")
    session.add(task)
    session.commit()
    return {"task_id": task.id}
```

- [ ] **Step 2: CronJobs frontend**: ProTable with schedule, command, enabled toggle, and "立即执行" button that creates shell task.

- [ ] **Step 3: Commit** `git add -A && git commit -m "feat: cron management — read/write system crontab + manual trigger"`

---

## Phase 5: Model & Strategy Management

### Task 5.1: Backend + Frontend

**Files:**
- Modify: `admin/server.py`
- Create: `admin/frontend/src/pages/Models.tsx`

- [ ] **Step 1: MLflow proxy + strategy editor endpoints**

```python
import requests

MLFLOW_API = "http://localhost:5000/api/2.0/mlflow"

@app.get("/api/admin/models")
def admin_models():
    try:
        r = requests.get(f"{MLFLOW_API}/registered-models/list", timeout=5)
        models = r.json().get("registered_models", [])
        result = []
        for m in models:
            name = m["name"]
            rv = requests.post(f"{MLFLOW_API}/model-versions/search", json={"filter": f"name='{name}'"}, timeout=5)
            versions = rv.json().get("model_versions", [])
            result.append({"name": name, "versions": [{"version": v["version"], "stage": v.get("current_stage",""), "run_id": v.get("run_id","")} for v in versions]})
        return result
    except Exception as e:
        return {"error": str(e)}

@app.post("/api/admin/models/train")
def admin_train_model(model_name: str, market: str = "us"):
    script_map = {("us_tech","us"): "scripts/train_us_tech_v1_explicit.py", ("hk_tech","hk"): "scripts/train_hk_tech_v1.py"}
    script = script_map.get((model_name, market), "")
    if not script:
        return {"error": f"No training script for {model_name}/{market}"}, 400
    cmd = f"cd /opt/quant-prod && PYTHONPATH=/opt/quant-prod .venv/bin/python3 {script}"
    session = get_session()
    task = Task(type="shell", params={"cmd": cmd}, status="pending")
    session.add(task)
    session.commit()
    return {"task_id": task.id}

@app.get("/api/admin/strategies")
def admin_strategies():
    files = glob.glob("/opt/quant-prod/strategies/*.py")
    return [{"name": os.path.basename(f), "path": f} for f in sorted(files)]

@app.get("/api/admin/strategies/{name}")
def admin_strategy_read(name: str):
    path = f"/opt/quant-prod/strategies/{name}"
    if not os.path.isfile(path) or not name.endswith(".py"):
        return {"error": "Invalid strategy name"}, 400
    with open(path) as f:
        return {"name": name, "source": f.read()}

@app.put("/api/admin/strategies/{name}")
def admin_strategy_save(name: str, source: str = ""):
    path = f"/opt/quant-prod/strategies/{name}"
    if not name.endswith(".py"):
        return {"error": "Invalid strategy name"}, 400
    with open(path, "w") as f:
        f.write(source)
    return {"status": "saved"}
```

- [ ] **Step 2: Models frontend**: Tabs: Models (ProTable with train button + MLflow iframe), Strategies (list + click to open code editor Drawer with save).

- [ ] **Step 3: Commit** `git add -A && git commit -m "feat: model & strategy management — MLflow proxy + code editor"`

---

## Phase 6: Factor Management

### Task 6.1: Backend + Frontend

**Files:**
- Modify: `admin/server.py`
- Create: `admin/frontend/src/pages/Factors.tsx`

- [ ] **Step 1: Factor endpoints with market coverage**

```python
from factors.registry import FactorRegistry

@app.get("/api/admin/factors")
def admin_factors():
    reg = FactorRegistry()
    active = reg.get_active()
    if active.empty:
        return []
    client = bigquery.Client(project="deductive-notch-495015-c2")
    coverage = {}
    try:
        cov_rows = client.query("""
            SELECT factor_id,
              CASE WHEN STARTS_WITH(symbol, 'US.') THEN 'us'
                   WHEN STARTS_WITH(symbol, 'HK.') THEN 'hk' ELSE 'crypto' END AS market,
              COUNT(DISTINCT symbol) AS symbols,
              MIN(date) AS min_date, MAX(date) AS max_date, COUNT(*) AS total_rows
            FROM quant.factor_values GROUP BY factor_id, market ORDER BY factor_id, market
        """).result()
        for c in cov_rows:
            fid = c.factor_id
            coverage.setdefault(fid, []).append({
                "market": c.market, "symbols": c.symbols,
                "min_date": str(c.min_date) if c.min_date else None,
                "max_date": str(c.max_date) if c.max_date else None,
                "total_rows": c.total_rows,
            })
    except Exception:
        pass
    result = []
    for _, row in active.iterrows():
        fid = row["factor_id"]
        result.append({
            "factor_id": fid, "name": row.get("name",""), "category": row.get("category",""),
            "status": row.get("status","active"),
            "markets": [c["market"] for c in coverage.get(fid, [])],
            "coverage": coverage.get(fid, []),
            "latest_ic": row.get("ic_mean"),
        })
    return result

@app.post("/api/admin/factors/compute")
def admin_factor_compute(source: str = "tech", market: str = "us",
                         start: str = "2020-01-01", end: str = "2026-06-03"):
    cmd = (f"cd /opt/quant-prod && PYTHONPATH=/opt/quant-prod "
           f".venv/bin/python3 scripts/compute_factors_batch.py "
           f"--source {source} --market {market} --start {start} --end {end}")
    session = get_session()
    task = Task(type="shell", params={"cmd": cmd}, status="pending")
    session.add(task)
    session.commit()
    return {"task_id": task.id}
```

- [ ] **Step 2: Factors frontend**: ProTable with factor_id, name, category, status Tag, markets (colored Tags), IC, and "查看" button → Drawer with coverage table (market, symbols, date range, total rows). Also compute button with source/market/date inputs → creates task.

- [ ] **Step 3: Commit** `git add -A && git commit -m "feat: factor management — list with market coverage + batch compute trigger"`

---

## Phase 7: Polish & Deploy

### Task 7.1: Production build + systemd

- [ ] **Step 1: Build frontend**

```bash
cd /opt/quant-dev/admin/frontend && npm run build
```

- [ ] **Step 2: Add static serving to server.py**

```python
from fastapi.staticfiles import StaticFiles
DIST = os.path.join(os.path.dirname(__file__), "frontend", "dist")
if os.path.isdir(DIST):
    app.mount("/", StaticFiles(directory=DIST, html=True))
```

- [ ] **Step 3: Create systemd units**

`/etc/systemd/system/quant-admin.service`:
```ini
[Unit]
Description=Quant Admin Platform
After=network.target
[Service]
User=quant
WorkingDirectory=/opt/quant-prod
ExecStart=/opt/quant-prod/.venv/bin/python3 -m uvicorn admin.server:app --host 0.0.0.0 --port 8091
Restart=always
[Install]
WantedBy=multi-user.target
```

`/etc/systemd/system/quant-admin-worker.service`:
```ini
[Unit]
Description=Quant Admin Worker
After=network.target
[Service]
User=quant
WorkingDirectory=/opt/quant-prod
ExecStart=/opt/quant-prod/.venv/bin/python3 admin/worker.py
Restart=always
[Install]
WantedBy=multi-user.target
```

- [ ] **Step 4: Enable and start**

```bash
sudo systemctl daemon-reload
sudo systemctl enable quant-admin quant-admin-worker
sudo systemctl start quant-admin quant-admin-worker
```

### Task 7.2: Integration test

- [ ] Verify all 6 module pages render
- [ ] Test experiment start/stop/restart end-to-end → task queue → worker executes
- [ ] Test log browser with real logs + WebSocket tail
- [ ] Test factor detail drawer with coverage data
- [ ] Commit + push

```bash
git add -A && git commit -m "chore: admin platform systemd units + deploy"
git push origin feature/admin-platform
```

---

## Summary

| Phase | Content | Tasks |
|-------|---------|-------|
| 0 | Project scaffold (React + FastAPI + SQLite + worker) | 3 |
| 1 | Experiment management | 1 |
| 2 | Data map + collector status | 1 |
| 3 | Log browser (JSON + WebSocket) | 1 |
| 4 | Cron management | 1 |
| 5 | Model & strategy management | 1 |
| 6 | Factor management | 1 |
| 7 | Polish & deploy | 2 |

**Total: 11 tasks.** Each task has API code + frontend functional specs. Ready for subagent-driven execution.
