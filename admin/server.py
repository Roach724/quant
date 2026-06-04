"""Quant Admin Platform — FastAPI server."""

import subprocess, json as _json, os, glob
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query, Depends, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, field_serializer
from sqlalchemy.orm import Session
from datetime import datetime
from typing import Optional

from google.cloud import bigquery

from admin.models import init_db, get_session, Task
from live.experiment_manager import ExperimentManager

DB_SESSION_DEP = Depends(get_session)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(title="Quant Admin", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Request / response schemas ────────────────────────────────────────────────

class TaskCreate(BaseModel):
    type: str = "shell"
    command: str


class TaskOut(BaseModel):
    id: int
    type: str
    status: str
    params: Optional[dict] = None
    result: Optional[str] = None
    created_at: Optional[datetime] = None
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None

    model_config = {"from_attributes": True}

    @field_serializer("created_at", "started_at", "finished_at")
    def serialize_dt(self, dt: Optional[datetime]) -> Optional[str]:
        return dt.isoformat() if dt else None


# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.get("/api/tasks")
def list_tasks(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    status: Optional[str] = Query(None),
    db: Session = DB_SESSION_DEP,
):
    q = db.query(Task).order_by(Task.created_at.desc())
    if status:
        q = q.filter(Task.status == status)
    total = q.count()
    tasks = q.offset(offset).limit(limit).all()
    return {"total": total, "tasks": [TaskOut.model_validate(t) for t in tasks]}


@app.post("/api/tasks")
def create_task(body: TaskCreate, db: Session = DB_SESSION_DEP):
    task = Task(
        type=body.type,
        status="pending",
        params={"command": body.command},
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    return TaskOut.model_validate(task)


@app.get("/api/admin/tasks/{task_id}")
def get_task(task_id: int, db: Session = DB_SESSION_DEP):
    task = db.get(Task, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return TaskOut.model_validate(task)


# ── Experiment management ─────────────────────────────────────────────────────

@app.get("/api/admin/experiments")
def admin_experiments():
    mgr = ExperimentManager()
    return [{
        "exp_id": e.id, "name": e.name, "type": e.type,
        "market": e.market, "strategy": e.strategy,
        "version": e.version, "status": e.status,
        "current_run": e.current_run, "config_path": e.config_path,
        "pid": mgr.get_pid(e.id),
    } for e in mgr.list()]


@app.post("/api/admin/experiments/{exp_id}/{action}")
def admin_experiment_action(exp_id: str, action: str):
    """start / stop / restart an experiment via task queue."""
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


# ── Data Map + Collector status ──────────────────────────────────────────────

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
    try:
        r = subprocess.run(
            ["systemctl", "is-active", "ws-collector"],
            capture_output=True, text=True, timeout=5,
        )
        status = r.stdout.strip()
    except Exception:
        status = "unknown"
    heartbeat = None
    try:
        with open("/var/log/quant/prod/collector/ws_collector.log") as f:
            lines = f.readlines()
            for line in reversed(lines[-100:]):
                if "HEARTBEAT" in line:
                    heartbeat = _json.loads(line).get("ts")
                    break
    except Exception:
        pass
    return {"ws_collector": status, "last_heartbeat": heartbeat}


@app.post("/api/admin/data/collector/{action}")
def admin_collector_action(action: str):
    if action not in ("start", "stop", "restart"):
        return {"error": f"Unknown action: {action}"}, 400
    cmd = f"sudo systemctl {action} ws-collector"
    session = get_session()
    task = Task(type="shell", params={"cmd": cmd}, status="pending")
    session.add(task)
    session.commit()
    return {"task_id": task.id, "status": "pending"}


# ── Cron Management ───────────────────────────────────────────────────────────

@app.get("/api/admin/cron")
def admin_cron_list():
    """Read system crontab, parse into structured jobs."""
    r = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
    lines = r.stdout.strip().split("\n") if r.stdout.strip() else []
    jobs = []
    for i, line in enumerate(lines):
        line = line.strip()
        if not line or line.startswith("#"):
            if line.startswith("#"):
                jobs.append({
                    "index": i, "raw": line, "enabled": False,
                    "schedule": "", "command": "", "comment": line.lstrip("# ")
                })
            continue
        parts = line.split(None, 5)
        if len(parts) >= 6:
            jobs.append({
                "index": i, "raw": line, "enabled": True,
                "schedule": " ".join(parts[:5]),
                "command": parts[5], "comment": "",
            })
    return jobs


@app.post("/api/admin/cron")
def admin_cron_save(jobs: list[dict]):
    """Save updated crontab."""
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
def admin_cron_run(command: str = Query("")):
    """Manually trigger a cron command via task queue."""
    session = get_session()
    task = Task(type="shell", params={"cmd": command}, status="pending")
    session.add(task)
    session.commit()
    return {"task_id": task.id}


# ── Log Browser ───────────────────────────────────────────────────────────────

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
def admin_logs(
    module: str = Query("collector"),
    level: str = Query(""),
    search: str = Query(""),
    lines: int = Query(100),
):
    """Read log lines from /var/log/quant/prod/{module}/, filtered."""
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
                lvl = ""
                msg = line.strip()
                ts = ""
            if level and level.upper() != lvl.upper():
                continue
            if search and search.lower() not in msg.lower():
                continue
            result_lines.append({"ts": ts, "level": lvl, "msg": msg})
    result_lines.reverse()
    return {"module": module, "file": os.path.basename(log_file), "lines": result_lines}


@app.websocket("/ws/logs")
async def ws_logs(websocket: WebSocket, module: str = "collector"):
    """Real-time log tail via WebSocket."""
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
                        await websocket.send_json({
                            "ts": entry.get("ts", ""),
                            "level": entry.get("level", ""),
                            "msg": entry.get("msg", ""),
                        })
                    except Exception:
                        await websocket.send_json({"ts": "", "level": "", "msg": line.strip()})
                else:
                    await asyncio.sleep(0.5)
    except (WebSocketDisconnect, Exception):
        await websocket.close()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("admin.server:app", host="0.0.0.0", port=8092, reload=True)
