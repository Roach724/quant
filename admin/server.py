"""Quant Admin Platform — FastAPI server."""

import subprocess, json as _json, os, glob, logging
import requests
import pandas as pd
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query, Depends, WebSocket, WebSocketDisconnect, Body
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, field_serializer
from sqlalchemy.orm import Session
from datetime import datetime, timezone
from typing import Optional, Any

from google.cloud import bigquery

from admin.models import init_db, get_session, Task
from live.experiment_manager import ExperimentManager
from factors.registry import FactorRegistry

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


@app.get("/api/admin/experiments/{exp_id}/runs")
def admin_experiment_runs(exp_id: str):
    """Return run history for an experiment."""
    mgr = ExperimentManager()
    try:
        runs = mgr.runs(exp_id)
        return [{
            "run_id": r.run_id,
            "status": r.status,
            "started_at": r.started_at,
            "ended_at": r.ended_at,
        } for r in runs]
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Experiment '{exp_id}' not found")


@app.post("/api/admin/experiments/register")
def admin_experiment_register(data: dict):
    """Register a new experiment."""
    mgr = ExperimentManager()
    try:
        exp_id = mgr.register(
            exp_type=data.get("type", "live"),
            market=data.get("market", "us"),
            strategy=data.get("strategy", "ml"),
            version=int(data.get("version", 1)),
            config_path=data.get("config_path", ""),
            name=data.get("name", ""),
        )
        exp = mgr.get(exp_id)
        return {"exp_id": exp.id, "status": exp.status}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


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


@app.post("/api/admin/experiments/{exp_id}/clear")
def admin_experiment_clear(exp_id: str):
    """Clear all experiment data: BQ + state files + registry runs."""
    from google.cloud import bigquery as _bq

    mgr = ExperimentManager()

    # Make sure experiment exists
    try:
        exp = mgr.get(exp_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Experiment '{exp_id}' not found")

    results: dict[str, str] = {}

    # 1. Clear BQ data
    client = _bq.Client(project="deductive-notch-495015-c2")
    for table in ["experiment_equity", "experiment_trades", "experiment_runs"]:
        try:
            client.query(
                f"DELETE FROM quant.{table} WHERE exp_id='{exp_id}'"
            ).result()
            results[table] = "cleared"
        except Exception as e:
            results[table] = str(e)[:80]

    # 2. Clear state files
    strategy = exp.strategy
    state_dirs = [
        f"/var/quant/state/{strategy}/",
        f"/var/quant/state/{strategy}_hk/",
    ]
    shared_state_file = f"/var/quant/state/{strategy}.json"
    for d in state_dirs:
        if not os.path.isdir(d):
            continue
        for f in glob.glob(os.path.join(d, "*.json")):
            try:
                os.remove(f)
                results[f"state_{os.path.basename(f)}"] = "deleted"
            except Exception as e:
                results[f"state_{os.path.basename(f)}"] = str(e)[:80]

    # Also remove shared state file if it exists
    if os.path.isfile(shared_state_file):
        try:
            os.remove(shared_state_file)
            results[f"state_{strategy}.json"] = "deleted"
        except Exception as e:
            results[f"state_{strategy}.json"] = str(e)[:80]

    # 3. Reset registry runs
    mgr._data[exp_id]["runs"] = []
    mgr._data[exp_id]["current_run"] = None
    mgr._data[exp_id]["status"] = "pending"
    mgr._save()
    results["registry"] = "reset to pending"

    return {"status": "ok", "details": results}


# ── Data Map + Collector status ──────────────────────────────────────────────

@app.get("/api/admin/data/f10")
def admin_data_f10():
    """Return F10 collector status."""
    # Check running F10 processes
    try:
        r = subprocess.run(
            ["pgrep", "-f", "collect_futu_factors"],
            capture_output=True, text=True, timeout=5,
        )
        running_pids = [p for p in r.stdout.strip().split("\n") if p]
    except Exception:
        running_pids = []

    collectors = [
        {
            "name": "us_rating_summary",
            "description": "美股评级汇总 (collect-us-rating-summary)",
            "running": len(running_pids) > 0,
        },
        {
            "name": "us_insider_trade",
            "description": "美股内部人交易 (collect-us-insider-trade)",
            "running": False,
        },
    ]
    return collectors


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


@app.post("/api/admin/data/backfill")
def admin_data_backfill(market: str = "us", start: str = "2020-01-01", end: str = "2026-06-03"):
    """Trigger data backfill via worker."""
    cmd = (f"cd /opt/quant-prod && PYTHONPATH=/opt/quant-prod "
           f".venv/bin/python3 collectors/backfill.py "
           f"--market {market} --start {start} --end {end}")
    session = get_session()
    task = Task(type="shell", params={"cmd": cmd}, status="pending")
    session.add(task)
    session.commit()
    return {"task_id": task.id}


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

CRON_REGISTRY = os.environ.get(
    "CRON_REGISTRY_PATH",
    os.path.join(os.path.dirname(__file__), "..", "config", "cron_registry.json"),
)


@app.get("/api/admin/cron")
def admin_cron_list():
    """Read system crontab, merge with registry for names/descriptions."""
    # Load registry for metadata (names, descriptions)
    registry_jobs = {}
    resolved = os.path.abspath(CRON_REGISTRY)
    if os.path.isfile(resolved):
        try:
            with open(resolved) as f:
                data = _json.load(f)
            for j in data.get("jobs", []):
                cmd = j.get("command", "").strip()
                if cmd:
                    registry_jobs[cmd] = j
        except Exception:
            pass

    # Read system crontab (source of truth)
    r = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
    raw = r.stdout.strip()
    if not raw:
        return list(registry_jobs.values()) if registry_jobs else []

    lines = raw.split("\n")
    jobs = []
    for i, line in enumerate(lines):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(None, 5)
        if len(parts) >= 6:
            cmd = parts[5].strip()
            # Match by prefix (crontab may add >> redirect that registry lacks)
            meta = {}
            for reg_cmd, reg_job in registry_jobs.items():
                if cmd.startswith(reg_cmd) or reg_cmd.startswith(cmd.split(">>")[0].strip()):
                    meta = reg_job
                    break
            jobs.append({
                "index": i,
                "raw": line,
                "enabled": True,
                "schedule": " ".join(parts[:5]),
                "command": cmd,
                "name": meta.get("name", ""),
                "description": meta.get("description", ""),
            })
    return jobs


@app.post("/api/admin/cron")
def admin_cron_save(jobs: list[dict]):
    """Save updated cron jobs — write back to registry if it exists, else crontab."""
    resolved = os.path.abspath(CRON_REGISTRY)

    # If registry exists, save back to it
    if os.path.isfile(resolved):
        out = [{
            "name": j.get("name", ""),
            "description": j.get("description", ""),
            "schedule": j.get("schedule", ""),
            "command": j.get("command", ""),
            "enabled": j.get("enabled", False),
        } for j in jobs]
        os.makedirs(os.path.dirname(resolved), exist_ok=True)
        with open(resolved, "w") as f:
            _json.dump({"jobs": out}, f, ensure_ascii=False, indent=2)
        return {"status": "ok"}

    # Fallback: system crontab
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


@app.post("/api/admin/cron/add")
def admin_cron_add(job: dict = Body(...)):
    """Add a new cron job to the registry."""
    resolved = os.path.abspath(CRON_REGISTRY)
    os.makedirs(os.path.dirname(resolved), exist_ok=True)
    if os.path.isfile(resolved):
        with open(resolved) as f:
            data = _json.load(f)
    else:
        data = {"jobs": []}
    data.setdefault("jobs", []).append({
        "name": job.get("name", ""),
        "description": job.get("description", ""),
        "schedule": job.get("schedule", ""),
        "command": job.get("command", ""),
        "enabled": job.get("enabled", True),
    })
    with open(resolved, "w") as f:
        _json.dump(data, f, indent=2, ensure_ascii=False)
    return {"status": "ok"}


@app.put("/api/admin/cron/{index}")
def admin_cron_update(index: int, job: dict = Body(...)):
    """Update a cron job (full or partial — e.g. toggle enabled)."""
    resolved = os.path.abspath(CRON_REGISTRY)
    if not os.path.isfile(resolved):
        return {"error": "Cron registry not found"}, 404
    with open(resolved) as f:
        data = _json.load(f)
    if not (0 <= index < len(data.get("jobs", []))):
        return {"error": "Invalid index"}, 400
    target = data["jobs"][index]
    # Partial update: only overwrite fields present in request
    for key in ("name", "description", "schedule", "command", "enabled"):
        if key in job:
            target[key] = job[key]
    with open(resolved, "w") as f:
        _json.dump(data, f, indent=2, ensure_ascii=False)
    return {"status": "ok", "job": target}


@app.post("/api/admin/cron/run")
def admin_cron_run(command: str = Query("")):
    """Manually trigger a cron command via task queue."""
    session = get_session()
    task = Task(type="shell", params={"cmd": command}, status="pending")
    session.add(task)
    session.commit()
    return {"task_id": task.id}


@app.get("/api/admin/cron/{index}/history")
def admin_cron_history(index: int):
    """Return recent execution history from task queue."""
    session = get_session()
    tasks = (
        session.query(Task)
        .filter(Task.type.in_(["shell", "cron_run"]))
        .order_by(Task.created_at.desc())
        .limit(50)
        .all()
    )
    return [{
        "id": t.id,
        "status": t.status,
        "created_at": t.created_at.isoformat() if t.created_at else None,
        "started_at": t.started_at.isoformat() if t.started_at else None,
        "finished_at": t.finished_at.isoformat() if t.finished_at else None,
        "result": (t.result or "")[:200],
    } for t in tasks]


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
    start: str = Query(""),
    end: str = Query(""),
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
    # Parse optional time range filter
    ts_start = None
    ts_end = None
    if start:
        try:
            ts_start = datetime.fromisoformat(start.replace("Z", "+00:00"))
        except Exception:
            pass
    if end:
        try:
            ts_end = datetime.fromisoformat(end.replace("Z", "+00:00"))
        except Exception:
            pass

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
            # Time range filter
            if ts_start and ts:
                try:
                    line_ts = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                    if line_ts < ts_start:
                        continue
                except Exception:
                    pass
            if ts_end and ts:
                try:
                    line_ts = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                    if line_ts > ts_end:
                        continue
                except Exception:
                    pass
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


# ── Model & Strategy Management ──────────────────────────────────────────────

MLFLOW_API = "http://localhost:5000/api/2.0/mlflow"

@app.get("/api/admin/models")
def admin_models():
    """List registered models with versions from MLflow."""
    try:
        r = requests.get(f"{MLFLOW_API}/registered-models/search", timeout=5)
        models = r.json().get("registered_models", []) or []
        result = []
        for m in models:
            name = m["name"]
            rv = requests.get(
                f"{MLFLOW_API}/model-versions/search",
                params={"name": name},
                timeout=5,
            )
            versions = rv.json().get("model_versions", []) or []
            result.append({
                "name": name,
                "versions": [{
                    "version": v["version"],
                    "stage": v.get("current_stage", ""),
                    "run_id": v.get("run_id", ""),
                } for v in versions],
            })
        return result
    except Exception as e:
        return {"error": str(e)}


@app.get("/api/admin/models/{name}/history")
def admin_model_history(name: str):
    """Return training run history for a model with key metrics."""
    rv = requests.post(
        f"{MLFLOW_API}/model-versions/search",
        json={"filter": f"name='{name}'"},
        timeout=5,
    )
    versions = rv.json().get("model_versions", []) or []
    history = []
    for v in versions:
        try:
            run_r = requests.get(
                f"{MLFLOW_API}/runs/get",
                json={"run_id": v["run_id"]},
                timeout=5,
            )
            run_data = run_r.json().get("run", {}).get("data", {})
            metrics = {m["key"]: m["value"] for m in run_data.get("metrics", [])}
            params = {p["key"]: p["value"] for p in run_data.get("params", [])}
        except Exception:
            metrics = {}
            params = {}
        history.append({
            "version": v["version"],
            "run_id": v["run_id"],
            "rmse": metrics.get("rmse"),
            "ic": metrics.get("ic"),
            "dataset": params.get("dataset", ""),
            "n_features": int(params.get("n_features", 0)),
            "n_trials": int(params.get("n_trials", 0)),
        })
    return history


@app.post("/api/admin/models/train")
def admin_train_model(model_name: str, market: str = "us"):
    """Trigger model training via task queue."""
    script_map = {
        ("us_tech", "us"): "scripts/train_us_tech_v1_explicit.py",
        ("hk_tech", "hk"): "scripts/train_hk_tech_v1.py",
    }
    script = script_map.get((model_name, market), "")
    if not script:
        return {"error": f"No training script for {model_name}/{market}"}, 400
    cmd = f"cd /opt/quant-prod && PYTHONPATH=/opt/quant-prod .venv/bin/python3 {script}"
    session = get_session()
    task = Task(type="shell", params={"cmd": cmd}, status="pending")
    session.add(task)
    session.commit()
    return {"task_id": task.id}


@app.get("/api/admin/models/{name}/versions")
def admin_model_versions(name: str):
    """Get all versions of a model with metrics."""
    rv = requests.post(
        f"{MLFLOW_API}/model-versions/search",
        json={"filter": f"name='{name}'"},
        timeout=5,
    )
    versions = rv.json().get("model_versions", [])
    result = []
    for v in versions:
        try:
            run_r = requests.get(
                f"{MLFLOW_API}/runs/get",
                json={"run_id": v["run_id"]},
                timeout=5,
            )
            run_data = run_r.json().get("run", {}).get("data", {})
            metrics = {m["key"]: m["value"] for m in run_data.get("metrics", [])}
            params = {p["key"]: p["value"] for p in run_data.get("params", [])}
            run_info = run_r.json().get("run", {}).get("info", {})
            start_time = run_info.get("start_time", 0)
            end_time = run_info.get("end_time", 0)
            training_time = round((end_time - start_time) / 1000, 1) if end_time and start_time else None
        except Exception:
            metrics = {}
            params = {}
            training_time = None
        result.append({
            "version": v["version"],
            "stage": v.get("current_stage", ""),
            "run_id": v.get("run_id", ""),
            "rmse": metrics.get("rmse"),
            "ic": metrics.get("ic"),
            "n_features": int(params.get("n_features", 0)),
            "dataset": params.get("dataset", ""),
            "training_time": training_time,
        })
    return result


@app.post("/api/admin/models/{name}/stage")
def admin_model_stage(name: str, version: str = "", stage: str = ""):
    """Transition a model version to a new stage."""
    r = requests.post(
        f"{MLFLOW_API}/model-versions/transition-stage",
        json={"name": name, "version": version, "stage": stage},
        timeout=5,
    )
    return r.json()


@app.get("/api/admin/strategies")
def admin_strategies():
    """List strategy files in strategies/ directory."""
    files = glob.glob("/opt/quant-prod/strategies/*.py")
    return [{"name": os.path.basename(f), "path": f} for f in sorted(files)]


@app.get("/api/admin/strategies/{name}")
def admin_strategy_read(name: str):
    """Read a strategy source file."""
    path = f"/opt/quant-prod/strategies/{name}"
    if not os.path.isfile(path) or not name.endswith(".py"):
        return {"error": "Invalid strategy name"}, 400
    with open(path) as f:
        return {"name": name, "source": f.read()}


@app.put("/api/admin/strategies/{name}")
def admin_strategy_save(name: str, body: dict = Body(...)):
    """Save a strategy source file."""
    path = f"/opt/quant-prod/strategies/{name}"
    if not name.endswith(".py"):
        return {"error": "Invalid strategy name"}, 400
    with open(path, "w") as f:
        f.write(body.get("source", ""))
    return {"status": "saved"}


# ── Factor Management ──────────────────────────────────────────────────────────

@app.get("/api/admin/factors")
def admin_factors():
    """List all factors with market coverage from factor_values."""
    reg = FactorRegistry()
    markets = ["us", "hk", "crypto"]
    active_frames = []
    for m in markets:
        try:
            df = reg.get_active(market=m)
            if not df.empty:
                active_frames.append(df)
        except Exception:
            pass
    if not active_frames:
        return []
    active = pd.concat(active_frames, ignore_index=True)

    client = bigquery.Client(project="deductive-notch-495015-c2")
    # Get market coverage per factor
    coverage = {}
    try:
        cov_rows = client.query("""
            SELECT factor_id,
              CASE
                WHEN STARTS_WITH(symbol, 'HK.') THEN 'hk'
                WHEN REGEXP_CONTAINS(symbol, r'^[A-Z]{1,5}([-_][A-Z])?$') THEN 'us'
                WHEN REGEXP_CONTAINS(symbol, r'^[0-9]+$') THEN 'hk'
                ELSE 'crypto' END AS market,
              COUNT(DISTINCT symbol) AS symbols,
              MIN(date) AS min_date, MAX(date) AS max_date, COUNT(*) AS total_rows
            FROM quant.factor_values
            GROUP BY factor_id, market ORDER BY factor_id, market
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

    import math

    def _clean(val):
        """Convert NaN/Inf to None for JSON serialization."""
        if isinstance(val, float) and (math.isnan(val) or math.isinf(val)):
            return None
        return val

    result = []
    for _, row in active.iterrows():
        fid = row["factor_id"]
        is_active = row.get("is_active", True)
        result.append({
            "factor_id": fid, "name": row.get("name", ""),
            "category": row.get("category", ""),
            "status": "active" if is_active else "inactive",
            "markets": [c["market"] for c in coverage.get(fid, [])],
            "coverage": coverage.get(fid, []),
            "latest_ic": _clean(row.get("latest_ic_mean")),
        })
    return result


@app.post("/api/admin/factors/{factor_id}/toggle")
def admin_factor_toggle(factor_id: str, active: bool = True):
    """Activate or deactivate a factor."""
    from factors.registry import FactorRegistry
    reg = FactorRegistry()
    if active:
        reg.activate(factor_id)
    else:
        reg.deactivate(factor_id)
    return {"status": "ok", "factor_id": factor_id, "active": active}


@app.post("/api/admin/factors/{factor_id}/evaluate")
def admin_factor_evaluate(factor_id: str):
    """Trigger factor evaluation via task queue."""
    cmd = (
        f"cd /opt/quant-prod && PYTHONPATH=/opt/quant-prod "
        f".venv/bin/python3 -c \"from factors.registry import FactorRegistry; "
        f"FactorRegistry().evaluate('{factor_id}')\""
    )
    session = get_session()
    task = Task(type="shell", params={"cmd": cmd}, status="pending")
    session.add(task)
    session.commit()
    return {"task_id": task.id, "factor_id": factor_id}


@app.post("/api/admin/factors/compute")
def admin_factor_compute(source: str = "tech", market: str = "us",
                         start: str = "2020-01-01", end: str = "2026-06-03"):
    """Trigger factor batch computation via task queue."""
    cmd = (f"cd /opt/quant-prod && PYTHONPATH=/opt/quant-prod "
           f".venv/bin/python3 scripts/compute_factors_batch.py "
           f"--source {source} --market {market} --start {start} --end {end}")
    session = get_session()
    task = Task(type="shell", params={"cmd": cmd}, status="pending")
    session.add(task)
    session.commit()
    return {"task_id": task.id}


# ── Dashboard APIs (migrated from dashboard/server.py) ─────────────────────────

_DB_BQ_PROJECT = "deductive-notch-495015-c2"
_DB_BQ = lambda: bigquery.Client(project=_DB_BQ_PROJECT)
_DB_TABLE = lambda name: f"{_DB_BQ_PROJECT}.quant.{name}"


def _db_serialize(x: Any) -> Any:
    """Inline replacement for dashboard _serialize(x)."""
    if x is None:
        return None
    return x.isoformat() if hasattr(x, "isoformat") else str(x)


def _db_row_to_dict(r, names):
    """Inline replacement for dashboard _row_to_dict(r, names)."""
    return {n: _db_serialize(getattr(r, n, None)) for n in names}


# ---------------------------------------------------------------------------
# GET /api/admin/dashboard/experiments — latest equity snapshot per experiment
# ---------------------------------------------------------------------------
@app.get("/api/admin/dashboard/experiments")
async def dash_experiments(type: str = ""):
    client = _DB_BQ()
    prefix_filter = ""
    if type:
        prefix_filter = f"AND exp_id LIKE '{type}_%'"
    query = f"""
        SELECT * EXCEPT (rn)
        FROM (
            SELECT *,
                   ROW_NUMBER() OVER (PARTITION BY exp_id ORDER BY ts DESC) AS rn
            FROM {_DB_TABLE("experiment_equity")}
            WHERE NOT STARTS_WITH(exp_id, "test_") {prefix_filter}
        )
        WHERE rn = 1
        ORDER BY ts DESC
    """
    try:
        rows = client.query(query).result()
        return [{"exp_id": row.exp_id, "ts": _db_serialize(row.ts),
                 "bar": row.bar, "equity": row.equity, "cash": row.cash,
                 "portfolio_value": row.portfolio_value, "daily_pnl": row.daily_pnl,
                 "drawdown": row.drawdown}
                for row in rows]
    except Exception as exc:
        logging.getLogger(__name__).error("dash_experiments query error: %s", exc)
        return []


# ---------------------------------------------------------------------------
# GET /api/admin/dashboard/experiments/meta — experiment tracker metadata
# ---------------------------------------------------------------------------
@app.get("/api/admin/dashboard/experiments/meta")
async def dash_experiments_meta():
    from pathlib import Path
    import os as _os
    _quant_root = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
    exp_dir = Path(_quant_root) / "output/live/experiments"
    if not exp_dir.exists():
        return []
    result = []
    for exp_path in sorted(exp_dir.iterdir()):
        if not exp_path.is_dir():
            continue
        exp_file = exp_path / "experiment.json"
        if not exp_file.exists():
            continue
        try:
            meta = _json.loads(exp_file.read_text())
            sessions_file = exp_path / "investment_sessions.json"
            sessions = []
            if sessions_file.exists():
                sessions = _json.loads(sessions_file.read_text())
            result.append({
                "exp_id": meta.get("experiment_id", exp_path.name),
                "name": meta.get("name", ""),
                "status": meta.get("status", "unknown"),
                "created_at": meta.get("created_at", ""),
                "sessions": len(sessions),
            })
        except Exception:
            pass
    return result


# ---------------------------------------------------------------------------
# GET /api/admin/dashboard/equity/{exp_id} — equity time-series for one experiment
# ---------------------------------------------------------------------------
@app.get("/api/admin/dashboard/equity/{exp_id}")
async def dash_equity_series(exp_id: str, run_id: str = ""):
    client = _DB_BQ()
    if not run_id:
        latest_q = f"""
            SELECT run_id FROM {_DB_TABLE("experiment_equity")}
            WHERE exp_id = @exp_id
            ORDER BY ts DESC LIMIT 1
        """
        latest_rows = list(client.query(latest_q, job_config=bigquery.QueryJobConfig(
            query_parameters=[bigquery.ScalarQueryParameter("exp_id", "STRING", exp_id)]
        )).result())
        if latest_rows:
            run_id = latest_rows[0].run_id or ""
    run_filter = f"AND run_id = '{run_id}'" if run_id else ""
    query = f"""
        SELECT ts, bar, equity, cash, portfolio_value, daily_pnl, drawdown, run_id
        FROM {_DB_TABLE("experiment_equity")}
        WHERE exp_id = @exp_id {run_filter}
        ORDER BY bar ASC
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[bigquery.ScalarQueryParameter("exp_id", "STRING", exp_id)]
    )
    try:
        rows = client.query(query, job_config=job_config).result()
        return [_db_row_to_dict(r, ["ts", "bar", "equity", "cash",
                                     "portfolio_value", "daily_pnl", "drawdown", "run_id"])
                for r in rows]
    except Exception as exc:
        logging.getLogger(__name__).error("dash_equity_series query error for %s: %s", exp_id, exc)
        return []


# ---------------------------------------------------------------------------
# GET /api/admin/dashboard/trades/{exp_id} — recent trades for one experiment
# ---------------------------------------------------------------------------
@app.get("/api/admin/dashboard/trades/{exp_id}")
async def dash_trades(exp_id: str, limit: int = 200, run_id: str = ""):
    client = _DB_BQ()
    if not run_id:
        latest_q = f"""
            SELECT run_id FROM {_DB_TABLE("experiment_trades")}
            WHERE exp_id = '{exp_id}'
            ORDER BY ts DESC LIMIT 1
        """
        latest_rows = list(client.query(latest_q).result())
        if latest_rows:
            run_id = latest_rows[0].run_id or ""
    run_filter = f"AND run_id = @run_id" if run_id else ""
    query = f"""
        SELECT ts, bar, symbol, side, qty, price, commission
        FROM {_DB_TABLE("experiment_trades")}
        WHERE exp_id = @exp_id {run_filter}
        ORDER BY ts DESC
        LIMIT @limit
    """
    params = [
        bigquery.ScalarQueryParameter("exp_id", "STRING", exp_id),
        bigquery.ScalarQueryParameter("limit", "INT64", limit),
    ]
    if run_id:
        params.append(bigquery.ScalarQueryParameter("run_id", "STRING", run_id))
    job_config = bigquery.QueryJobConfig(query_parameters=params)
    try:
        rows = client.query(query, job_config=job_config).result()
        result = []
        for r in rows:
            d = _db_row_to_dict(r, ["ts", "bar", "symbol", "side",
                                     "qty", "price", "commission"])
            if "hk" in exp_id and d.get("symbol"):
                d["symbol"] = d["symbol"].zfill(5)
            result.append(d)
        return result
    except Exception as exc:
        logging.getLogger(__name__).error("dash_trades query error for %s: %s", exp_id, exc)
        return []


# ---------------------------------------------------------------------------
# GET /api/admin/dashboard/experiments/{exp_id}/positions — current positions (FIFO)
# ---------------------------------------------------------------------------
@app.get("/api/admin/dashboard/experiments/{exp_id}/positions")
async def dash_experiment_positions(exp_id: str, run_id: str = ""):
    from collections import defaultdict
    client = _DB_BQ()
    if not run_id:
        latest_q = f"""
            SELECT run_id FROM {_DB_TABLE("experiment_trades")}
            WHERE exp_id = '{exp_id}'
            ORDER BY ts DESC LIMIT 1
        """
        latest_rows = list(client.query(latest_q).result())
        if latest_rows:
            run_id = latest_rows[0].run_id or ""
    run_filter = f"AND run_id = '{run_id}'" if run_id else ""
    trades_q = f"""
        SELECT symbol, side, qty, price, ts
        FROM {_DB_TABLE("experiment_trades")}
        WHERE exp_id = '{exp_id}' {run_filter}
        ORDER BY ts
    """
    rows = list(client.query(trades_q).result())
    if not rows:
        return []

    lots = defaultdict(list)
    for r in rows:
        sym = r.symbol
        qty = float(r.qty)
        price = float(r.price)
        if r.side == "buy":
            lots[sym].append({"qty": qty, "price": price})
        else:
            remaining = qty
            while remaining > 0 and lots[sym]:
                lot = lots[sym][0]
                if lot["qty"] <= remaining:
                    remaining -= lot["qty"]
                    lots[sym].pop(0)
                else:
                    lot["qty"] -= remaining
                    remaining = 0

    if not lots:
        return []

    result = []
    for sym, sym_lots in lots.items():
        total_qty = sum(l["qty"] for l in sym_lots)
        if total_qty <= 0:
            continue
        total_cost = sum(l["qty"] * l["price"] for l in sym_lots)
        avg_cost = total_cost / total_qty

        us_prefix = sym.startswith("US.")
        if us_prefix:
            market = "us"
            bare = sym[3:]
        elif sym.startswith("HK."):
            market = "hk"
            bare = sym[3:]
        else:
            market = "hk" if "hk" in exp_id else "us"
            bare = sym
        prefix = "US." if market == "us" else "HK."
        if market == "hk":
            bare = bare.zfill(5)
        bq_sym = f"{prefix}{bare}"
        table = _DB_TABLE(f"{market}_bars_5m")
        try:
            price_q = f"""
                SELECT close FROM `{table}`
                WHERE symbol = '{bq_sym}'
                ORDER BY timestamp DESC LIMIT 1
            """
            price_rows = list(client.query(price_q).result())
            current_price = float(price_rows[0].close) if price_rows else avg_cost
        except Exception:
            current_price = avg_cost

        pnl = (current_price - avg_cost) * total_qty
        pnl_pct = (current_price - avg_cost) / avg_cost * 100 if avg_cost > 0 else 0
        result.append({
            "symbol": bare,
            "qty": round(total_qty, 2),
            "avg_cost": round(avg_cost, 2),
            "current_price": round(current_price, 2),
            "pnl": round(pnl, 2),
            "pnl_pct": round(pnl_pct, 2),
        })
    return result


# ---------------------------------------------------------------------------
# GET /api/admin/dashboard/experiments/{exp_id}/runs — run history
# ---------------------------------------------------------------------------
@app.get("/api/admin/dashboard/experiments/{exp_id}/runs")
async def dash_experiment_runs(exp_id: str):
    client = _DB_BQ()
    query = f"""
        SELECT run_id, status, started_at, ended_at, base_run
        FROM {_DB_TABLE("experiment_runs")}
        WHERE exp_id = '{exp_id}'
        ORDER BY started_at DESC
    """
    try:
        rows = client.query(query).result()
        return [_db_row_to_dict(r, ["run_id", "status", "started_at", "ended_at", "base_run"])
                for r in rows]
    except Exception as exc:
        logging.getLogger(__name__).error("dash_experiment_runs query error for %s: %s", exp_id, exc)
        return []


# ── Paper Run Dashboard API ──

@app.get("/api/admin/dashboard/paper-runs")
async def dash_paper_runs(limit: int = 50):
    client = _DB_BQ()
    try:
        query = f"""
            SELECT run_id, name, strategy, market, status, n_periods,
                   created_at, error_msg
            FROM (
              SELECT *, ROW_NUMBER() OVER (PARTITION BY run_id ORDER BY created_at DESC) AS rn
              FROM {_DB_TABLE("paper_runs")}
            )
            WHERE rn = 1
            ORDER BY created_at DESC
            LIMIT {min(limit, 200)}
        """
        rows = client.query(query).result()
        names = ["run_id", "name", "strategy", "market", "status",
                 "n_periods", "created_at", "error_msg"]
        return [_db_row_to_dict(r, names) for r in rows]
    except Exception as e:
        logging.getLogger(__name__).error("dash_paper_runs query failed: %s", e)
        return []


@app.get("/api/admin/dashboard/paper-runs/{run_id}")
async def dash_paper_run_detail(run_id: str):
    client = _DB_BQ()
    try:
        run_query = f"""
            SELECT run_id, name, strategy, market, status, n_periods,
                   config_json, created_at, error_msg
            FROM {_DB_TABLE("paper_runs")}
            WHERE run_id = '{run_id}'
            ORDER BY created_at DESC LIMIT 1
        """
        run_rows = list(client.query(run_query).result())
        if not run_rows:
            return {"error": "not found", "run_id": run_id}
        run_names = ["run_id", "name", "strategy", "market", "status",
                     "n_periods", "config_json", "created_at", "error_msg"]
        run = _db_row_to_dict(run_rows[0], run_names)

        metrics = {}
        try:
            m_query = f"""
                SELECT *
                FROM {_DB_TABLE("paper_metrics")}
                WHERE run_id = '{run_id}'
            """
            m_rows = list(client.query(m_query).result())
            if m_rows:
                m_names = ["run_id", "total_return", "annual_return", "annual_vol",
                          "sharpe", "sortino", "max_drawdown", "calmar",
                          "win_rate", "total_trades", "profit_factor",
                          "start_equity", "end_equity", "computed_at"]
                metrics = _db_row_to_dict(m_rows[0], m_names)
        except Exception as e:
            logging.getLogger(__name__).error("dash_paper_metrics query failed: %s", e)
            metrics = {"error": str(e)}

        equity = []
        try:
            e_query = f"""
                SELECT ts, bar, equity, cash, portfolio_value, daily_pnl, drawdown
                FROM {_DB_TABLE("experiment_equity")}
                WHERE exp_id = '{run_id}'
                ORDER BY bar
            """
            e_rows = client.query(e_query).result()
            e_names = ["ts", "bar", "equity", "cash", "portfolio_value", "daily_pnl", "drawdown"]
            equity = [_db_row_to_dict(r, e_names) for r in e_rows]
        except Exception:
            pass

        trades = []
        try:
            t_query = f"""
                SELECT ts, bar, symbol, side, qty, price, commission
                FROM {_DB_TABLE("experiment_trades")}
                WHERE exp_id = '{run_id}'
                ORDER BY bar
                LIMIT 500
            """
            t_rows = client.query(t_query).result()
            t_names = ["ts", "bar", "symbol", "side", "qty", "price", "commission"]
            trades = [_db_row_to_dict(r, t_names) for r in t_rows]
            if run.get("market", "").lower() == "hk":
                for t in trades:
                    if t.get("symbol"):
                        t["symbol"] = t["symbol"].zfill(5)
        except Exception:
            pass

        return {"run": run, "metrics": metrics, "equity": equity, "trades": trades}
    except Exception as e:
        logging.getLogger(__name__).error("dash_paper_run_detail failed: %s", e)
        return {"error": str(e), "run_id": run_id}


# ── Pipeline (data freshness) API ──

@app.get("/api/admin/dashboard/pipeline")
async def dash_pipeline():
    client = _DB_BQ()
    result: dict = {
        "us": None, "hk": None,
        "us_open": False, "hk_open": False,
        "ts": datetime.now(timezone.utc).isoformat(),
    }
    now = datetime.now(timezone.utc)
    result["us_open"] = (
        now.weekday() < 5 and
        datetime(now.year, now.month, now.day, 13, 30, tzinfo=timezone.utc) <= now <=
        datetime(now.year, now.month, now.day, 20, 0, tzinfo=timezone.utc)
    )
    result["hk_open"] = (
        now.weekday() < 5 and
        datetime(now.year, now.month, now.day, 1, 30, tzinfo=timezone.utc) <= now <=
        datetime(now.year, now.month, now.day, 8, 0, tzinfo=timezone.utc)
    )
    try:
        q = f"""
            SELECT MAX(
              TIMESTAMP(DATETIME(timestamp), "America/New_York")
            ) AS latest FROM {_DB_TABLE("us_bars_5m")}
            WHERE timestamp >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 24 HOUR)
        """
        rows = list(client.query(q).result())
        if rows and rows[0].latest:
            result["us"] = _db_serialize(rows[0].latest)
    except Exception as exc:
        logging.getLogger(__name__).error("dash_pipeline us query error: %s", exc)
    try:
        q = f"""
            SELECT MAX(
              TIMESTAMP_SUB(timestamp, INTERVAL 8 HOUR)
            ) AS latest FROM {_DB_TABLE("hk_bars_5m")}
            WHERE timestamp >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 24 HOUR)
        """
        rows = list(client.query(q).result())
        if rows and rows[0].latest:
            result["hk"] = _db_serialize(rows[0].latest)
    except Exception as exc:
        logging.getLogger(__name__).error("dash_pipeline hk query error: %s", exc)
    return result


# ── Market Data API ──

@app.get("/api/admin/dashboard/market/symbols/{market}")
async def dash_market_symbols(market: str):
    import yaml
    from pathlib import Path
    config_path = Path("/opt/quant-dev/config/symbols.yaml")
    cfg = yaml.safe_load(config_path.read_text())
    syms = cfg.get("markets", {}).get(market, {}).get("symbols", [])
    prefix = f"{'US' if market == 'us' else 'HK'}."
    return [s.replace(prefix, "") for s in syms if s.startswith(prefix)]


@app.get("/api/admin/dashboard/market/{market}/{symbol}")
async def dash_market_bars(market: str, symbol: str, limit: int = 78):
    client = _DB_BQ()
    table = _DB_TABLE(f"{market}_bars_5m")
    full_symbol = f"{'US' if market == 'us' else 'HK'}.{symbol}"
    if market == "hk":
        ts_expr = "TIMESTAMP_SUB(timestamp, INTERVAL 8 HOUR)"
    else:
        ts_expr = 'TIMESTAMP(DATETIME(timestamp), "America/New_York")'
    query = f"""
        WITH dedup AS (
          SELECT {ts_expr} AS timestamp, open, high, low, close, volume,
            ROW_NUMBER() OVER (PARTITION BY symbol, timestamp ORDER BY _ingest_time DESC NULLS LAST) AS rn
          FROM `{table}`
          WHERE symbol = '{full_symbol}'
            AND {ts_expr} >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 2 DAY)
        )
        SELECT timestamp, open, high, low, close, volume
        FROM dedup WHERE rn = 1
        ORDER BY timestamp DESC
        LIMIT {limit}
    """
    rows = list(client.query(query).result())
    rows.reverse()
    return [{"ts": _db_serialize(r.timestamp), "o": r.open, "h": r.high,
             "l": r.low, "c": r.close, "v": r.volume} for r in rows]


# ── Static file serving (production build, after all API routes) ──────────────

from fastapi.staticfiles import StaticFiles
import os as _os

DIST = _os.path.join(_os.path.dirname(__file__), "frontend", "dist")
if _os.path.isdir(DIST):
    app.mount("/", StaticFiles(directory=DIST, html=True), name="static")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("admin.server:app", host="0.0.0.0", port=8092, reload=True)
