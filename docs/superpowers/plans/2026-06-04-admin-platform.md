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

```python
from live.experiment_manager import ExperimentManager

@app.get("/api/admin/experiments")
def admin_experiments():
    mgr = ExperimentManager()
    return [{"exp_id": e.id, "name": e.name, "type": e.type,
             "market": e.market, "strategy": e.strategy,
             "version": e.version, "status": e.status,
             "current_run": e.current_run, "config_path": e.config_path,
             "pid": mgr.get_pid(e.id)}
            for e in mgr.list()]


@app.post("/api/admin/experiments/{exp_id}/{action}")
def admin_experiment_action(exp_id: str, action: str):
    """start / stop / restart an experiment."""
    cmd_map = {
        "start": f"cd /opt/quant-prod && PYTHONPATH=/opt/quant-prod .venv/bin/python3 live/exp_cli.py start {exp_id}",
        "stop": f"cd /opt/quant-prod && PYTHONPATH=/opt/quant-prod .venv/bin/python3 live/exp_cli.py stop {exp_id}",
        "restart": f"cd /opt/quant-prod && PYTHONPATH=/opt/quant-prod .venv/bin/python3 live/exp_cli.py restart {exp_id}",
    }
    if action not in cmd_map:
        return {"error": f"Unknown action: {action}"}, 400

    session = get_session()
    task = Task(type="shell", params={"cmd": cmd_map[action]}, status="pending")
    session.add(task)
    session.commit()
    return {"task_id": task.id, "status": "pending"}
```

Replace the Experiments page placeholder in App.tsx with the component.

- [ ] **Step 3: Commit**

```bash
cd /opt/quant-dev && git add -A && git commit -m "feat: experiment list + start/stop/restart via task queue"
```

---

## Phase 2-6: Remaining Modules

### Task 2.1: Data Map (BQ table browser)

**Files:**
- Create: `admin/frontend/src/pages/DataMap.tsx`
- Modify: `admin/server.py`

- [ ] Add endpoint `GET /api/admin/data/tables` that queries `INFORMATION_SCHEMA.TABLES` and `INFORMATION_SCHEMA.COLUMNS`

- [ ] Build ProTable displaying: table_name, row_count, size_bytes, description, last_write, schema columns

### Task 2.2: Data Collection Status

- [ ] Add endpoint `GET /api/admin/data/collectors` returning ws_collector status (from `systemctl is-active ws-collector`) and latest heartbeat

- [ ] Add ws_collector start/stop buttons with market-hour warning

- [ ] Add data freshness indicators per BQ table

### Task 3: Log Browser

**Files:**
- Create: `admin/frontend/src/pages/LogViewer.tsx`
- Modify: `admin/server.py`

- [ ] Add endpoint `GET /api/admin/logs?module=X&level=Y&search=Z&lines=100` reading from `/var/log/quant/prod/{module}/`

- [ ] Add WebSocket endpoint for real-time tail: `ws://host:8092/ws/logs?module=collector`

- [ ] Build frontend with module dropdown, level filter, search input, and scrollable log viewer

### Task 4: Cron Management

**Files:**
- Create: `admin/frontend/src/pages/CronJobs.tsx`
- Modify: `admin/server.py`

- [ ] Add endpoint `GET /api/admin/cron` reading system crontab via `crontab -l`

- [ ] Add endpoint `POST /api/admin/cron` writing updated crontab

- [ ] Add endpoint `POST /api/admin/cron/{name}/run` triggering immediate execution

- [ ] Build frontend with toggle, edit, and manual run buttons

### Task 5: Model & Strategy Management

**Files:**
- Create: `admin/frontend/src/pages/Models.tsx`
- Modify: `admin/server.py`

- [ ] Add MLflow API proxy endpoints: list models, versions, metrics

- [ ] Add iframe for MLflow UI (`http://localhost:5000`)

- [ ] Add strategy list/editor page:
  - `GET /api/admin/strategies` — list strategy files
  - `GET /api/admin/strategies/{name}` — read source
  - `PUT /api/admin/strategies/{name}` — save edited source

- [ ] Add training trigger: select model + params → create shell task

### Task 6: Factor Management

**Files:**
- Create: `admin/frontend/src/pages/Factors.tsx`
- Modify: `admin/server.py`

- [ ] Add endpoint `GET /api/admin/factors` querying FactorRegistry + market coverage from `factor_values`

- [ ] Add endpoint `POST /api/admin/factors/compute` triggering batch computation

- [ ] Build frontend with factor list, detail drawer (data coverage table), and compute button

---

## Phase 7: Polish & Deploy

### Task 7.1: Frontend build + production deploy

- [ ] `npm run build` → output to `admin/frontend/dist/`

- [ ] Serve static files from FastAPI: `app.mount("/", StaticFiles(directory="admin/frontend/dist", html=True))`

- [ ] Create systemd unit for admin server and worker

- [ ] Deploy to `/opt/quant-prod/admin/`

### Task 7.2: Integration test

- [ ] Verify all 6 module pages render
- [ ] Test experiment start/stop/restart end-to-end
- [ ] Test task queue: create → pending → running → done
- [ ] Test log browser with real log files
- [ ] Test cron list/edit/sync

---

## Summary

| Phase | Content | Tasks |
|-------|---------|-------|
| 0 | Project scaffold (React + FastAPI + SQLite + worker) | 3 |
| 1 | Experiment management | 1 |
| 2 | Data map + collection status | 2 |
| 3 | Log browser | 1 |
| 4 | Cron management | 1 |
| 5 | Model & strategy management | 1 |
| 6 | Factor management | 1 |
| 7 | Polish & deploy | 2 |

**Total: 12 tasks**
