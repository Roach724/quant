"""Quant Admin Platform — FastAPI server."""

import subprocess, json as _json, os, glob, logging, re
from pathlib import Path
import requests
import pandas as pd
from contextlib import asynccontextmanager

logger = logging.getLogger(__name__)

from fastapi import FastAPI, HTTPException, Query, Depends, WebSocket, WebSocketDisconnect, Body, Request
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


def _startup_auto_heal():
    """On server startup, scan all experiments and mark dead runs as 'failed'.

    This handles experiments that were killed by node reboot, OOM, or other
    external forces. Marking them as 'failed' ensures the user sees they need
    manual attention rather than silently completing.
    """
    try:
        from live.experiment_manager import ExperimentManager
        mgr = ExperimentManager()
        exps = mgr.list()
        healed = 0
        for exp in exps:
            if exp.has_active_run:
                run_ids = mgr.startup_heal(exp.id)
                if run_ids:
                    logger.info(
                        "Startup heal: %s runs=%s marked failed (process dead)",
                        exp.id, run_ids,
                    )
                    healed += len(run_ids)
        if healed:
            logger.info("Startup heal complete: %d runs marked failed", healed)
    except Exception:
        logger.exception("Startup auto-heal failed (non-fatal)")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    _startup_auto_heal()
    yield
    from admin.models import cleanup_session
    cleanup_session()


app = FastAPI(title="Quant Admin", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def db_session_cleanup(request, call_next):
    """Clean up SQLAlchemy scoped session after every request."""
    try:
        response = await call_next(request)
        return response
    finally:
        from admin.models import cleanup_session
        cleanup_session()


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
@app.get("/api/admin/health")
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
    # Auto-heal all experiments before listing
    exps = mgr.list()
    for e in exps:
        if e.has_active_run:
            mgr.auto_heal(e.id)
    # Re-list after healing
    exps = mgr.list()
    return [{
        "exp_id": e.id, "name": e.name, "type": e.type,
        "market": e.market, "strategy": e.strategy,
        "version": e.version, "status": e.status,
        "has_active_run": e.has_active_run,
        "total_runs": e.total_runs,
        "current_run": e.current_run,
        "active_run_id": e.active_run.run_id if e.active_run else None,
        "config_path": e.config_path,
        "pid": mgr.get_pid(e.id),
        "created_at": e.created_at,
        "latest_run_at": e.runs[0].started_at if e.runs else None,
    } for e in mgr.list()]


@app.get("/api/admin/experiments/{exp_id}/runs")
def admin_experiment_runs(exp_id: str):
    """Return run history for an experiment. Auto-heals stale runs."""
    mgr = ExperimentManager()
    try:
        # Auto-heal: mark dead-PID runs as completed
        healed = mgr.auto_heal(exp_id)
        if healed:
            logger.info("Auto-healed runs for %s: %s", exp_id, healed)
        runs = mgr.runs(exp_id)
        return [{
            "run_id": r.run_id,
            "status": r.status,
            "started_at": r.started_at,
            "ended_at": r.ended_at,
            "base_run": r.base_run,
        } for r in runs]
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Experiment '{exp_id}' not found")


@app.post("/api/admin/experiments/{exp_id}/runs/{run_id}/stop")
def admin_experiment_run_stop(exp_id: str, run_id: str):
    """Manually stop a specific run."""
    mgr = ExperimentManager()
    try:
        mgr.stop_run(exp_id, run_id)
        # Stop via PID file (Docker: no systemd)
        import signal as _sig
        pid_file = f"/var/quant/state/{exp_id}.pid"
        if os.path.exists(pid_file):
            try:
                with open(pid_file) as pf:
                    pid = int(pf.read().strip())
                os.killpg(os.getpgid(pid), _sig.SIGTERM)
                os.remove(pid_file)
            except Exception:
                pass
            pass
        # Also kill via PID as fallback
        pid = mgr.get_pid(exp_id)
        if pid:
            import os as _os, signal as _sig
            try:
                _os.kill(pid, _sig.SIGTERM)
                import time as _time
                _time.sleep(1)
                try:
                    _os.kill(pid, _sig.SIGKILL)
                except OSError:
                    pass
            except OSError:
                pass
            mgr.set_pid(exp_id, None)
        return {"status": "ok", "run_id": run_id, "new_status": "stopped"}
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Experiment '{exp_id}' not found")
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ── Run-level endpoints ────────────────────────────────────────────────────────

@app.post("/api/admin/experiments/{exp_id}/runs/{run_id}/start")
def admin_experiment_run_start(exp_id: str, run_id: str):
    """Start (or resume) a specific run via task queue."""
    mgr = ExperimentManager()
    try:
        exp = mgr.get(exp_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Experiment '{exp_id}' not found")

    # Only block if THIS run is already running
    active = exp.active_run
    if active and active.run_id == run_id:
        raise HTTPException(status_code=409, detail="该 Run 已在运行中")
    # If a different run is active, stop it first
    if active and active.run_id != run_id:
        raise HTTPException(status_code=409, detail=f"已有其他活跃 Run ({active.run_id[:16]}...)，请先停止")

    cmd = (
        f"cd /opt/quant && PYTHONPATH=/opt/quant "
        f"python3 live/exp_cli.py start {exp_id} --resume-run {run_id}"
    )
    session = get_session()
    task = Task(type="shell", params={"cmd": cmd, "cron_command": cmd}, status="pending")
    session.add(task)
    session.commit()
    return {"task_id": task.id, "run_id": run_id}


@app.delete("/api/admin/experiments/{exp_id}/runs/{run_id}")
def admin_experiment_run_delete(exp_id: str, run_id: str):
    """Permanently delete a run and all associated data."""
    mgr = ExperimentManager()
    try:
        exp = mgr.get(exp_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Experiment '{exp_id}' not found")

    # Only block if THIS specific run is currently running
    active = exp.active_run
    if active and active.run_id == run_id:
        raise HTTPException(status_code=409, detail="活跃 Run 无法删除，请先停止")

    result = mgr.delete_run(exp_id, run_id)
    return {"status": "ok", "run_id": run_id, "details": result}


@app.post("/api/admin/experiments/{exp_id}/runs/{run_id}/clear-state")
def admin_experiment_run_clear_state(exp_id: str, run_id: str):
    """Clear state & checkpoint for a run (keeps BQ/logs)."""
    mgr = ExperimentManager()
    try:
        exp = mgr.get(exp_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Experiment '{exp_id}' not found")

    if exp.has_active_run:
        raise HTTPException(status_code=409, detail="存在活跃 Run，无法清除状态")

    result = mgr.clear_run_state(exp_id, run_id)
    return {"status": "ok", "run_id": run_id, "details": result}


@app.get("/api/admin/experiments/{exp_id}/runs/{run_id}/equity")
def admin_experiment_run_equity(exp_id: str, run_id: str):
    """Return equity curve for a specific run."""
    client = _DB_BQ()
    q = f"""
        SELECT ts, bar, equity, cash, portfolio_value, daily_pnl, drawdown
        FROM {_DB_TABLE("experiment_equity")}
        WHERE exp_id='{exp_id}' AND run_id='{run_id}'
        ORDER BY bar
    """
    try:
        rows = list(client.query(q).result())
        return [{
            "ts": _db_serialize(r.ts),
            "bar": r.bar,
            "equity": r.equity,
            "cash": r.cash,
            "portfolio_value": r.portfolio_value,
            "daily_pnl": r.daily_pnl,
            "drawdown": getattr(r, "drawdown", None),
        } for r in rows]
    except Exception as exc:
        logging.getLogger(__name__).error(
            "admin_experiment_run_equity query error for %s/%s: %s", exp_id, run_id, exc
        )
        return []


@app.get("/api/admin/experiments/{exp_id}/runs/{run_id}/positions")
def admin_experiment_run_positions(exp_id: str, run_id: str):
    """Return current FIFO positions for a specific run."""
    from collections import defaultdict
    client = _DB_BQ()
    trades_q = f"""
        SELECT symbol, side, qty, price, ts
        FROM {_DB_TABLE("experiment_trades")}
        WHERE exp_id='{exp_id}' AND run_id='{run_id}'
        ORDER BY ts
    """
    try:
        rows = list(client.query(trades_q).result())
    except Exception as exc:
        logging.getLogger(__name__).error(
            "admin_experiment_run_positions query error for %s/%s: %s", exp_id, run_id, exc
        )
        return []

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
        avg_cost = total_cost / total_qty if total_qty > 0 else 0
        result.append({
            "symbol": sym,
            "qty": round(total_qty, 4),
            "avg_cost": round(avg_cost, 4),
        })
    return result


# ── Experiment registration ───────────────────────────────────────────────────

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


@app.post("/api/admin/experiments/create-from-config")
def admin_experiment_create_from_config(body: dict = Body(...)):
    """Create a new experiment by copying a config template."""
    import shutil, yaml as _yaml
    template = body.get("template", "")
    new_id = body.get("exp_id", "")
    if not template or not new_id:
        raise HTTPException(status_code=400, detail="Missing 'template' or 'exp_id'")
    config_dir = Path("/opt/quant/live/configs")
    template_path = config_dir / template
    if not template_path.exists():
        raise HTTPException(status_code=404, detail=f"Template '{template}' not found")
    # Store experiment instances in a subdirectory, separate from templates
    instances_dir = config_dir / "instances"
    instances_dir.mkdir(parents=True, exist_ok=True)
    new_path = instances_dir / f"{new_id}.yaml"
    if new_path.exists():
        raise HTTPException(status_code=409, detail=f"Config '{new_id}.yaml' already exists")
    # Copy template and update experiment.id in the copy
    shutil.copy2(template_path, new_path)
    # Inject exp_id into the config YAML so run.py uses it for log file naming
    try:
        cfg = _yaml.safe_load(new_path.read_text()) or {}
        cfg.setdefault("experiment", {})["id"] = new_id
        new_path.write_text(_yaml.dump(cfg, default_flow_style=False, allow_unicode=True))
    except Exception:
        pass
    mgr = ExperimentManager()
    try:
        exp_id = mgr.register(
            exp_type=body.get("type", "live"),
            market=body.get("market", "us"),
            strategy=body.get("strategy", "ml"),
            version=int(body.get("version", 1)),
            config_path=str(new_path),
            name=body.get("name", new_id),
            exp_id=new_id,
        )
        return {"exp_id": exp_id, "config_path": str(new_path)}
    except ValueError as e:
        new_path.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/admin/experiments/configs")
def admin_experiment_configs():
    """List all experiment config templates (YAML files)."""
    config_dir = Path("/opt/quant/live/configs")
    if not config_dir.exists():
        return []
    configs = []
    for f in sorted(config_dir.glob("*.yaml")):
        # Skip files in instances/ (experiment instances, not templates)
        if "instances" in f.parts:
            continue
        st = f.stat()
        mtime = datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).isoformat()
        ctime = datetime.fromtimestamp(st.st_ctime, tz=timezone.utc).isoformat()
        configs.append({
            "name": f.name,
            "path": str(f),
            "size": st.st_size,
            "created_at": ctime if ctime < mtime else mtime,  # ensure created ≤ updated
            "updated_at": mtime,
        })
    return configs


@app.delete("/api/admin/experiments/configs/{name}")
def admin_experiment_config_delete(name: str):
    """Delete a config template file."""
    import shutil
    if "/" in name or name.startswith("instances"):
        raise HTTPException(status_code=400, detail="Invalid config name")
    path = Path("/opt/quant/live/configs") / name
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"Config '{name}' not found")
    backup = path.with_suffix(path.suffix + ".del")
    shutil.move(str(path), str(backup))
    return {"status": "ok", "backup": str(backup)}


@app.get("/api/admin/experiments/configs/{name}")
def admin_experiment_config_get(name: str):
    """Read a single config template file."""
    if "/" in name or name.startswith("instances"):
        raise HTTPException(status_code=400, detail="Invalid config name")
    path = Path("/opt/quant/live/configs") / name
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"Config '{name}' not found")
    return {"name": name, "content": path.read_text()}


@app.put("/api/admin/experiments/configs/{name}")
def admin_experiment_config_put(name: str, body: dict = Body(...)):
    """Create or update a config template file. Backs up existing."""
    import shutil
    if "/" in name or name.startswith("instances"):
        raise HTTPException(status_code=400, detail="Invalid config name")
    content = body.get("content", "")
    if not content:
        raise HTTPException(status_code=400, detail="Missing 'content'")
    path = Path("/opt/quant/live/configs") / name
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        backup = path.with_suffix(path.suffix + ".bak")
        shutil.copy2(path, backup)
    path.write_text(content)
    return {"status": "ok", "name": name}


@app.post("/api/admin/experiments/configs/{name}/rename")
def admin_experiment_config_rename(name: str, body: dict = Body(...)):
    """Rename a config template. Returns error if target already exists."""
    import shutil as _sh
    new_name = body.get("new_name", "").strip()
    if not new_name:
        raise HTTPException(status_code=400, detail="Missing 'new_name'")
    if "/" in name or name.startswith("instances") or "/" in new_name or new_name.startswith("instances"):
        raise HTTPException(status_code=400, detail="Invalid config name")
    if not new_name.endswith(".yaml"):
        new_name += ".yaml"
    base_dir = Path("/opt/quant/live/configs")
    old_path = base_dir / name
    new_path = base_dir / new_name
    if not old_path.exists():
        raise HTTPException(status_code=404, detail=f"Config '{name}' not found")
    if new_path.exists():
        raise HTTPException(status_code=409, detail=f"Config '{new_name}' already exists")
    _sh.move(str(old_path), str(new_path))
    return {"status": "ok", "old_name": name, "new_name": new_name}


@app.post("/api/admin/experiments/{exp_id}/clear")
def admin_experiment_clear(exp_id: str):
    """Clear all experiment data: BQ + state files + registry runs."""
    from google.cloud import bigquery as _bq

    mgr = ExperimentManager()
    try:
        exp = mgr.get(exp_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Experiment '{exp_id}' not found")

    results: dict[str, str] = {}

    # 1. Clear BQ data
    client = _bq.Client(project="deductive-notch-495015-c2")
    for table in ["experiment_equity", "experiment_trades", "experiment_runs"]:
        try:
            client.query(f"DELETE FROM quant.{table} WHERE exp_id='{exp_id}'").result()
            results[table] = "cleared"
        except Exception as e:
            results[table] = str(e)[:80]

    # 2. Clear state files
    strategy = exp.strategy
    state_dirs = [f"/var/quant/state/{strategy}/", f"/var/quant/state/{strategy}_hk/"]
    shared_state_file = f"/var/quant/state/{strategy}.json"
    for d in state_dirs:
        if not os.path.isdir(d): continue
        for f in glob.glob(os.path.join(d, "*.json")):
            try:
                os.remove(f)
                results[f"state_{os.path.basename(f)}"] = "deleted"
            except Exception as e:
                results[f"state_{os.path.basename(f)}"] = str(e)[:80]
    if os.path.isfile(shared_state_file):
        try:
            os.remove(shared_state_file)
            results[f"state_{strategy}.json"] = "deleted"
        except Exception as e:
            results[f"state_{strategy}.json"] = str(e)[:80]

    # 3. Reset registry runs
    mgr._data[exp_id]["runs"] = []
    mgr._data[exp_id]["current_run"] = None
    mgr._data[exp_id]["status"] = "paused"
    mgr._save()
    results["registry"] = "reset to paused"

    return {"status": "ok", "details": results}


# ── Experiment state machine ─────────────────────────────────────────────────
#  registered → paused ⇄ running → stopped → archived
#  - start:    paused/registered → running
#  - stop:     running → stopped
#  - restart:  stop old → start new
#  - archive:  stopped → archived (read-only)
#  - clear:    wipe data, reset to paused
#  - delete:   wipe everything + unregister
# ──────────────────────────────────────────────────────────────────────────────


@app.post("/api/admin/experiments/{exp_id}/delete")
def admin_experiment_delete(exp_id: str):
    """Delete experiment: BQ + state + output + logs + unregister.
    Blocked if there is an active run."""
    from google.cloud import bigquery as _bq
    from pathlib import Path
    import shutil, signal, time

    mgr = ExperimentManager()
    try:
        exp = mgr.get(exp_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Experiment '{exp_id}' not found")

    # Block if active run
    if exp.has_active_run:
        raise HTTPException(
            status_code=409,
            detail=f"存在活跃 Run ({exp.active_run.run_id if exp.active_run else '?'})，无法删除实验。请先停止当前 Run。"
        )

    # Kill process if running
    pid = mgr.get_pid(exp_id)
    if pid:
        try:
            os.kill(pid, signal.SIGTERM)
            time.sleep(1)
            try: os.kill(pid, signal.SIGKILL)
            except OSError: pass
        except OSError: pass

    results: dict[str, str] = {}

    # 1. Delete BQ data
    client = _bq.Client(project="deductive-notch-495015-c2")
    for table in ["experiment_equity", "experiment_trades", "experiment_runs"]:
        try:
            client.query(f"DELETE FROM quant.{table} WHERE exp_id='{exp_id}'").result()
            results[table] = "deleted"
        except Exception as e:
            results[table] = str(e)[:80]

    # 2. Delete state directories
    strategy = exp.strategy
    for d in [f"/var/quant/state/{strategy}/", f"/var/quant/state/{strategy}_hk/"]:
        if os.path.isdir(d):
            try:
                shutil.rmtree(d)
                results[f"state_{os.path.basename(d.rstrip('/'))}"] = "deleted"
            except Exception as e:
                results[f"state_{os.path.basename(d.rstrip('/'))}"] = str(e)[:80]
    for f in [f"/var/quant/state/{strategy}.json"]:
        if os.path.isfile(f):
            try:
                os.remove(f)
                results["state_shared"] = "deleted"
            except Exception as e:
                results["state_shared"] = str(e)[:80]

    # 3. Delete output/live directories (including experiments meta)
    output_base = Path("/opt/quant/output/live")
    if output_base.exists():
        for d in output_base.iterdir():
            if d.is_dir() and exp_id in d.name:
                try:
                    shutil.rmtree(d)
                    results[f"output_{d.name}"] = "deleted"
                except Exception as e:
                    results[f"output_{d.name}"] = str(e)[:80]
        # Also clean experiment meta directory
        exp_meta = output_base / "experiments" / exp_id
        if exp_meta.exists():
            try:
                shutil.rmtree(exp_meta)
                results["output_experiments_meta"] = "deleted"
            except Exception as e:
                results["output_experiments_meta"] = str(e)[:80]

    # 4. Delete experiment logs
    for log_root in LOG_ROOTS:
        log_dir = Path(log_root) / "live"
        if log_dir.exists():
            for f in log_dir.glob(f"*{exp_id}*.log*"):
                try:
                    f.unlink()
                    results[f"log_{f.name}"] = "deleted"
                except Exception as e:
                    results[f"log_{f.name}"] = str(e)[:80]

    # 4.5 Delete experiment instance config
    config_path = Path("/opt/quant/live/configs/instances") / f"{exp_id}.yaml"
    if not config_path.exists():
        # Fallback: old location (pre-instances refactor)
        config_path = Path("/opt/quant") / exp.config_path
    if config_path.exists():
        try:
            # Backup to .del first, then remove
            backup = config_path.with_suffix(config_path.suffix + ".del")
            shutil.copy2(config_path, backup)
            config_path.unlink()
            results["config_file"] = f"deleted (backup: {backup.name})"
        except Exception as e:
            results["config_file"] = str(e)[:80]

    # 5. Unregister
    try:
        mgr.delete(exp_id)
        results["registry"] = "unregistered"
    except Exception as e:
        results["registry"] = str(e)[:80]

    return {"status": "ok", "details": results}


@app.post("/api/admin/experiments/{exp_id}/{action}")
def admin_experiment_action(exp_id: str, action: str):
    """start / stop / restart an experiment via task queue."""
    cmd_map = {
        "start": f"cd /opt/quant && PYTHONPATH=/opt/quant python3 live/exp_cli.py start {exp_id}",
        "stop": f"cd /opt/quant && PYTHONPATH=/opt/quant python3 live/exp_cli.py stop {exp_id}",
        "restart": f"cd /opt/quant && PYTHONPATH=/opt/quant python3 live/exp_cli.py restart {exp_id}",
    }
    if action not in cmd_map:
        return {"error": f"Unknown action: {action}"}, 400
    session = get_session()
    task = Task(type="shell", params={"cmd": cmd_map[action]}, status="pending")
    session.add(task)
    session.commit()
    return {"task_id": task.id, "status": "pending"}


@app.get("/api/admin/experiments/{exp_id}/config")
def admin_experiment_config(exp_id: str):
    """Return the experiment's YAML config file content."""
    mgr = ExperimentManager()
    try:
        exp = mgr.get(exp_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Experiment '{exp_id}' not found")
    config_path = Path("/opt/quant") / exp.config_path
    if not config_path.exists():
        raise HTTPException(status_code=404, detail=f"Config file not found: {config_path}")
    return {"exp_id": exp_id, "path": str(config_path), "content": config_path.read_text()}


@app.put("/api/admin/experiments/{exp_id}/config")
def admin_experiment_config_update(exp_id: str, body: dict = Body(...)):
    """Update the experiment's YAML config file. Backs up old version."""
    import shutil
    mgr = ExperimentManager()
    try:
        exp = mgr.get(exp_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Experiment '{exp_id}' not found")
    config_path = Path("/opt/quant") / exp.config_path
    if not config_path.exists():
        raise HTTPException(status_code=404, detail=f"Config file not found: {config_path}")
    content = body.get("content", "")
    if not content:
        raise HTTPException(status_code=400, detail="Missing 'content' in request body")
    # Backup
    backup_path = config_path.with_suffix(config_path.suffix + ".bak")
    shutil.copy2(config_path, backup_path)
    config_path.write_text(content)
    return {"status": "ok", "path": str(config_path), "backup": str(backup_path)}


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
    # Use a simple module-level cache with long TTL to avoid repeated INFO_SCHEMA queries
    import time as _time
    cache_key = "_bq_tables_cache"
    now = _time.time()
    if hasattr(admin_data_tables, cache_key):
        cached_data, cached_ts = getattr(admin_data_tables, cache_key)
        if now - cached_ts < 86400:  # 24-hour TTL
            return cached_data

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
    setattr(admin_data_tables, cache_key, (tables, now))
    return tables


@app.get("/api/admin/data/collectors")
def admin_data_collectors():
    """ws_collector status + last heartbeat + subscription stats + Futu quotas."""
    try:
        r = subprocess.run(
            ["supervisorctl", "status", "ws_collector"],
            capture_output=True, text=True, timeout=5,
        )
        status = "active" if "RUNNING" in r.stdout else "inactive"
    except Exception:
        status = "unknown"
    heartbeat = None
    subscriptions = 0
    buffer_size = 0
    bars_received = 0
    try:
        with open("/var/log/quant/prod/collector/ws_collector.log") as f:
            lines = f.readlines()
            import re as _re
            for line in reversed(lines[-100:]):
                if "HEARTBEAT" in line:
                    entry = _json.loads(line)
                    if heartbeat is None:
                        heartbeat = entry.get("ts")
                    msg = entry.get("msg", "")
                    m = _re.search(r"subscriptions=(\d+).*buffer=(\d+).*bars_received=(\d+)", msg)
                    if m:
                        subscriptions = int(m.group(1))
                        buffer_size = int(m.group(2))
                        bars_received = int(m.group(3))
                        break
    except Exception:
        pass

    # Futu API quota (real-time + history)
    rt_quota = None
    hist_quota = None
    try:
        r = subprocess.run(
            ["python3", "/opt/quant/scripts/quota_check.py"],
            capture_output=True, text=True, timeout=10,
        )
        if r.returncode == 0 and r.stdout.strip():
            # Futu prints debug logs to stdout mixed with our JSON
            # Find and extract JSON object from the output
            for line in r.stdout.strip().split('\n'):
                idx = line.find('{"rt"')
                if idx >= 0:
                    data = _json.loads(line[idx:])
                    rt_quota = data.get("rt")
                    hist_quota = data.get("hist")
                    break
        else:
            logging.getLogger(__name__).warning("quota_check failed: rc=%s stderr=%s", r.returncode, r.stderr[:200])
    except Exception as exc:
        logging.getLogger(__name__).exception("quota_check error")

    return {
        "ws_collector": status,
        "last_heartbeat": heartbeat,
        "subscriptions": subscriptions,
        "buffer": buffer_size,
        "bars_received": bars_received,
        "rt_quota": rt_quota,
        "hist_quota": hist_quota,
    }


@app.post("/api/admin/data/backfill")
def admin_data_backfill(
    market: str = "us",
    tables: str = Query("", description="Comma-separated table keys to backfill"),
    start: str = "2020-01-01",
    end: str = "2026-06-03",
    source: str = "auto",
):
    """Trigger data backfill via worker. Supports multiple tables, serial execution."""
    # Resolve table keys to (market, frequency) pairs
    selected = [t.strip() for t in tables.split(",") if t.strip()] if tables else []
    if not selected:
        selected = [f"{market}_bars_5m"]

    # Load symbols from SSOT for the market
    import yaml as _y2
    symbols_yaml_path = os.path.join(os.path.dirname(__file__), "..", "config", "symbols.yaml")
    symbols_list: list[str] = []
    try:
        with open(symbols_yaml_path) as sf:
            ssot = _y2.safe_load(sf)
        market_syms = ssot.get("markets", {}).get(mkt, {}).get("symbols", [])
        symbols_list = [s for s in market_syms if isinstance(s, str)]
    except Exception:
        pass
    symbols_str = ",".join(symbols_list) if symbols_list else ""

    # Auto-resolve source by market if not explicitly set
    resolved_source = source
    task_ids = []
    for key in selected:
        parts = key.split("_bars_")
        if len(parts) != 2:
            continue
        mkt, freq = parts
        if mkt not in ("us", "hk"):
            continue
        src = resolved_source if resolved_source != "auto" else ("yfinance" if mkt == "us" else "futu_stock")
        mkdir_log = f"mkdir -p /var/log/quant/prod/backfill"
        log_file = f"/var/log/quant/prod/backfill/{mkt}_{freq}.log"
        cmd = (f"{mkdir_log} && cd /opt/quant && PYTHONPATH=/opt/quant "
               f"python3 collectors/backfill.py "
               f"--symbols \"{symbols_str}\" --frequency {freq} --source {src} --start {start} --end {end} "
               f"2>&1 | while IFS= read -r l; do echo \"$(date -u +%Y-%m-%dT%H:%M:%SZ) $l\"; done "
               f"| tee -a {log_file}")
        session = get_session()
        task = Task(type="shell", params={"cmd": cmd}, status="pending")
        session.add(task)
        session.commit()
        task_ids.append(task.id)
    return {"task_ids": task_ids, "count": len(task_ids)}


@app.get("/api/admin/data/backfill/options")
def admin_data_backfill_options():
    """Return available backfill categories and tables."""
    return {
        "categories": [
            {
                "key": "kline",
                "label": "K线数据",
                "tables": [
                    {"key": "us_bars_5m", "label": "US 5分钟K线", "market": "us"},
                    {"key": "us_bars_1d", "label": "US 日线", "market": "us"},
                    {"key": "hk_bars_5m", "label": "HK 5分钟K线", "market": "hk"},
                    {"key": "hk_bars_1d", "label": "HK 日线", "market": "hk"},
                    {"key": "hk_bars_index_5m", "label": "HK 指数 5分钟K线", "market": "hk"},
                    {"key": "hk_bars_index_1d", "label": "HK 指数 日线", "market": "hk"},
                    {"key": "us_bars_index_5m", "label": "US 指数 5分钟K线", "market": "us"},
                    {"key": "us_bars_index_1d", "label": "US 指数 日线", "market": "us"},
                ],
            },
        ],
        "sources": [
            {"key": "auto", "label": "自动 (US=yfinance, HK=futu_stock)"},
            {"key": "yfinance", "label": "yfinance (US)"},
            {"key": "yfinancehk", "label": "yfinance (HK)"},
            {"key": "futu_stock", "label": "Futu (US/HK 股票)"},
            {"key": "alpaca", "label": "Alpaca (US, 需auth)"},
        ],
    }


@app.get("/api/admin/data/backfill/progress")
def admin_data_backfill_progress(
    task_id: int = Query(0),
    market: str = Query("us"),
    freq: str = Query("1d"),
):
    """Read backfill progress from the log file."""
    import re as _re
    log_file = f"/var/log/quant/prod/backfill/{market}_{freq}.log"
    try:
        with open(log_file) as f:
            lines = f.readlines()
    except Exception:
        return {"progress": None, "lines": []}

    # Extract last progress line
    progress = None
    recent = []
    for line in lines[-20:]:
        recent.append(line.rstrip())
        m = _re.search(r"Progress: (\d+)/(\d+) symbols", line)
        if m:
            progress = {"done": int(m.group(1)), "total": int(m.group(2))}

    # Also check if task completed
    task_status = None
    if task_id:
        session = get_session()
        t = session.query(Task).filter(Task.id == task_id).first()
        if t:
            task_status = t.status

    return {"progress": progress, "task_status": task_status, "lines": recent[-5:]}


@app.post("/api/admin/data/collector/{action}")
def admin_collector_action(action: str):
    if action not in ("start", "stop", "restart"):
        return {"error": f"Unknown action: {action}"}, 400
    cmd = f"supervisorctl {action} ws_collector"
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
    """Read cron jobs from persistent file (/var/data/crontab.txt)."""
    Crontab_File = "/var/data/crontab.txt"
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

    # Read from persistent crontab file
    if not os.path.isfile(Crontab_File):
        return list(registry_jobs.values()) if registry_jobs else []
    raw = open(Crontab_File).read().strip()
    if not raw:
        return list(registry_jobs.values()) if registry_jobs else []

    lines = raw.split("\n")
    jobs = []
    # Scan log dir for latest log per job name
    log_dir = Path("/var/log/quant/prod/cron")
    log_files = {}
    if log_dir.exists():
        for lf in sorted(log_dir.iterdir(), reverse=True):
            if lf.suffix == ".gz":
                continue
            base = lf.stem.split(".log")[0].rsplit("-", 2)[0] if re.search(r'-\d{8}', lf.stem) else lf.stem
            if base not in log_files:
                log_files[base] = lf
    for i, line in enumerate(lines):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(None, 5)
        if len(parts) >= 6:
            cmd = parts[5].strip()
            meta = {}
            for reg_cmd, reg_job in registry_jobs.items():
                if cmd.startswith(reg_cmd) or reg_cmd.startswith(cmd.split(">>")[0].strip()):
                    meta = reg_job
                    break
            job_name = meta.get("name", "")
            latest = log_files.get(job_name)
            jobs.append({
                "index": i,
                "raw": line,
                "enabled": True,
                "schedule": " ".join(parts[:5]),
                "command": cmd,
                "name": job_name,
                "description": meta.get("description", ""),
                "latest_log": latest.name if latest else None,
                "last_run": datetime.fromtimestamp(latest.stat().st_mtime, tz=timezone.utc).isoformat() if latest else None,
            })
    return jobs


@app.post("/api/admin/cron")
def admin_cron_save(jobs: list[dict]):
    """Save cron jobs — write to persistent file + sync to crontab."""
    Crontab_File = "/var/data/crontab.txt"
    resolved = os.path.abspath(CRON_REGISTRY)

    # Save registry metadata if it exists
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

    # Write crontab lines to persistent file
    lines = []
    for j in jobs:
        if j.get("raw"):
            lines.append(j["raw"])
        elif j.get("enabled"):
            lines.append(f"{j['schedule']} {j['command']}")
    crontab_content = "\n".join(lines) + "\n"
    with open(Crontab_File, "w") as f:
        f.write(crontab_content)

    # Sync to system crontab (for cron daemon inside container)
    subprocess.run(["crontab", Crontab_File], capture_output=True)
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
def admin_cron_run(command: str = Query(""), name: str = Query("")):
    """Manually trigger a cron command via task queue."""
    # Wrap with timestamped log redirect
    log_name = name or "cron"
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    log_file = f"/var/log/quant/prod/cron/{log_name}_{ts}.log"
    os.makedirs(os.path.dirname(log_file), exist_ok=True)
    wrapped = f"({command}) >> {log_file} 2>&1"
    session = get_session()
    task = Task(type="shell", params={"cmd": wrapped, "cron_command": command}, status="pending")
    session.add(task)
    session.commit()
    return {"task_id": task.id, "log_file": log_name + "_" + ts + ".log"}


@app.get("/api/admin/cron/{index}/history")
def admin_cron_history(index: int, command: str = Query("")):
    """Return recent execution history filtered by cron command."""
    session = get_session()
    query = session.query(Task).order_by(Task.created_at.desc()).limit(50)
    if command:
        # Filter by cron_command stored in params JSON
        tasks = [
            t for t in query.all()
            if t.params and t.params.get("cron_command") == command
        ]
    else:
        # Legacy fallback: only tasks with cron_command set
        tasks = [
            t for t in query.all()
            if t.params and "cron_command" in (t.params or {})
        ]
    return [{
        "id": t.id,
        "status": t.status,
        "created_at": t.created_at.isoformat() if t.created_at else None,
        "started_at": t.started_at.isoformat() if t.started_at else None,
        "finished_at": t.finished_at.isoformat() if t.finished_at else None,
        "result": (t.result or "")[:200],
    } for t in tasks]


# ── Log Browser ───────────────────────────────────────────────────────────────

LOG_ROOTS = ["/var/log/quant/prod", "/var/log/quant/dev"]
LOG_MODULES = ["collector", "live", "paper_run", "factor", "cron", "train", "loader", "backfill", "quality", "adhoc"]


def _module_log_files(module: str) -> list[str]:
    """Collect all *.log files for a module across all LOG_ROOTS."""
    files: list[str] = []
    for root in LOG_ROOTS:
        path = os.path.join(root, module)
        if os.path.isdir(path):
            files.extend(glob.glob(os.path.join(path, "*.log")))
    return sorted(files, reverse=True)


@app.get("/api/admin/logs/modules")
def admin_log_modules():
    modules = []
    for mod in LOG_MODULES:
        files = _module_log_files(mod)
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
    file: str = Query(""),
):
    """Read log lines from /var/log/quant/{prod,dev}/{module}/, filtered."""
    files = _module_log_files(module)
    if not files:
        if not any(os.path.isdir(os.path.join(r, module)) for r in LOG_ROOTS):
            return {"error": f"Unknown module: {module}", "lines": []}
        return {"module": module, "lines": [], "file": None, "files": []}
    # If file param given, find matching file; else default to most recent
    if file:
        # file could be a basename or full path — find the matching one
        match = [f for f in files if f.endswith("/" + file) or f == file]
        log_file = match[0] if match else files[0]
    else:
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
    return {
        "module": module,
        "file": os.path.basename(log_file),
        "file_path": log_file,
        "files": [os.path.basename(f) for f in files],
        "lines": result_lines,
    }


@app.websocket("/ws/logs")
async def ws_logs(websocket: WebSocket, module: str = "collector"):
    """Real-time log tail via WebSocket."""
    await websocket.accept()
    files = _module_log_files(module)
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


# ── Log File Management ───────────────────────────────────────────────────────

@app.get("/api/admin/logs/files")
def admin_log_files(module: str = Query("collector")):
    """List all log files for a module with size and mtime."""
    import os as _os_stat
    files = []
    for fpath in _module_log_files(module):
        try:
            st = _os_stat.stat(fpath)
            files.append({
                "name": _os_stat.path.basename(fpath),
                "path": fpath,
                "size": st.st_size,
                "mtime": datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).isoformat(),
            })
        except OSError:
            pass
    return files


@app.delete("/api/admin/logs/files")
def admin_log_delete(module: str = Query("collector"), file: str = Query("")):
    """Delete a log file. Safe: running services continue writing to inode."""
    if not file:
        raise HTTPException(status_code=400, detail="Missing 'file' parameter")
    # Resolve basename to full path
    all_files = _module_log_files(module)
    target = None
    for f in all_files:
        if _os.path.basename(f) == file or f == file:
            target = f
            break
    if not target or not _os.path.isfile(target):
        raise HTTPException(status_code=404, detail=f"File '{file}' not found")
    try:
        _os.remove(target)
        return {"status": "ok", "deleted": file}
    except OSError as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Model & Strategy Management ──────────────────────────────────────────────


@app.get("/api/admin/models")
def admin_models():
    """List registered models with versions from MLflow."""
    try:
        models = ModelRegistry.list_all_models()
        # Return only name + basic version info (no metrics needed here)
        return [{
            "name": m["name"],
            "versions": [{
                "version": v["version"],
                "stage": v["stage"],
                "run_id": v["run_id"],
            } for v in m["versions"]],
        } for m in models]
    except Exception as e:
        return {"error": str(e)}


@app.get("/api/admin/models/{name}/history")
def admin_model_history(name: str):
    """Return training run history for a model with key metrics."""
    versions = ModelRegistry.get_model_versions(name)
    return [{
        "version": v["version"],
        "run_id": v["run_id"],
        "rmse": v.get("rmse"),
        "ic": v.get("rank_ic"),
        "dataset": v.get("dataset", ""),
        "n_features": v.get("n_features", 0),
        "n_trials": 0,
    } for v in versions]


@app.post("/api/admin/models/train")
def admin_train_model(model_name: str, market: str = "us", skip_tuning: bool = False,
                     config_name: str = ""):
    """Trigger model training via task queue. Logs to separate file per run."""
    # Use config_name if provided (from frontend ml/configs flow), else script_map
    if config_name:
        script = "scripts/train_ml.py"
    else:
        script_map = {
            ("us_tech", "us"): "scripts/train_us_tech_v1_explicit.py",
            ("hk_tech", "hk"): "scripts/train_hk_tech_v1.py",
        }
        script = script_map.get((model_name, market), "")
        if not script:
            return {"error": f"No training script for {model_name}/{market}"}, 400

    from datetime import datetime, timezone
    run_id = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    log_file = f"/var/log/quant/prod/train/{model_name}_{run_id}.log"

    flags = ""
    if skip_tuning:
        flags += " --skip-tuning"
    if config_name:
        flags += f" --config {config_name}"

    cmd = (
        f"mkdir -p /var/log/quant/prod/train && "
        f"cd /opt/quant && "
        f"PYTHONPATH=/opt/quant python3 {script}{flags} "
        f"2>&1 | tee {log_file}"
    )
    session = get_session()
    task = Task(type="shell", params={"cmd": cmd}, status="pending")
    session.add(task)
    session.commit()
    return {"task_id": task.id, "run_id": run_id}


@app.get("/api/admin/models/{name}/versions")
def admin_model_versions(name: str):
    """Get all versions of a model with metrics."""
    versions = ModelRegistry.get_model_versions(name)
    return [{
        "version": v["version"],
        "stage": v["stage"],
        "run_id": v["run_id"],
        "rmse": v.get("rmse"),
        "ic": v.get("rank_ic"),
        "n_features": v.get("n_features", 0),
        "dataset": v.get("dataset", ""),
        "training_time": v.get("training_time"),
    } for v in versions]


@app.post("/api/admin/models/{name}/stage")
def admin_model_stage(name: str, version: str = "", stage: str = ""):
    """Transition a model version to a new stage."""
    try:
        ModelRegistry.promote(name, int(version), stage)
        return {"status": "ok", "name": name, "version": version, "stage": stage}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/admin/models/{name}/versions/{version}")
def admin_model_version_delete(name: str, version: int):
    """Delete a model version from MLflow registry."""
    try:
        ModelRegistry.delete_version(name, version)
        return {"status": "ok", "name": name, "version": version}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/admin/strategies")
def admin_strategies():
    """List strategy files in strategies/ directory."""
    files = glob.glob("/opt/quant/strategies/*.py")
    result = []
    for f in sorted(files):
        st = os.stat(f)
        mtime = datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).isoformat()
        ctime = datetime.fromtimestamp(st.st_ctime, tz=timezone.utc).isoformat()
        result.append({
            "name": os.path.basename(f), "path": f,
            "created_at": ctime if ctime < mtime else mtime,
            "updated_at": mtime,
        })
    return result


@app.get("/api/admin/strategies/{name}")
def admin_strategy_read(name: str):
    """Read a strategy source file."""
    path = f"/opt/quant/strategies/{name}"
    if not os.path.isfile(path) or not name.endswith(".py"):
        return {"error": "Invalid strategy name"}, 400
    with open(path) as f:
        return {"name": name, "source": f.read()}


@app.put("/api/admin/strategies/{name}")
def admin_strategy_save(name: str, body: dict = Body(...)):
    """Save a strategy source file."""
    path = f"/opt/quant/strategies/{name}"
    if not name.endswith(".py"):
        return {"error": "Invalid strategy name"}, 400
    with open(path, "w") as f:
        f.write(body.get("source", ""))
    return {"status": "saved"}


@app.delete("/api/admin/strategies/{name}")
def admin_strategy_delete(name: str):
    """Delete a strategy file (backup to .del)."""
    import shutil
    path = f"/opt/quant/strategies/{name}"
    if not name.endswith(".py") or name == "__init__.py":
        return {"error": "Cannot delete this file"}, 400
    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail=f"Strategy '{name}' not found")
    shutil.move(path, path + ".del")
    return {"status": "ok", "backup": name + ".del"}


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
        f"cd /opt/quant && PYTHONPATH=/opt/quant "
        f"python3 -c \"from factors.registry import FactorRegistry; "
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
    cmd = (f"cd /opt/quant && PYTHONPATH=/opt/quant "
           f"python3 scripts/compute_factors_batch.py "
           f"--source {source} --market {market} --start {start} --end {end}")
    session = get_session()
    task = Task(type="shell", params={"cmd": cmd}, status="pending")
    session.add(task)
    session.commit()
    return {"task_id": task.id}


# ── ML Subsystem ──────────────────────────────────────────────────────────────

import yaml as _yaml
from admin.models import MlDataset as _MlDataset, MlConfig as _MlConfig
from ml.registry import ModelRegistry
from google.cloud import bigquery as _bq

_ML_CONFIG_DIR = Path(__file__).resolve().parent.parent / "ml" / "configs"

# ── Datasets ──────────────────────────────────────────────────────────────────

@app.get("/api/admin/ml/datasets")
def admin_ml_datasets():
    session = get_session()
    rows = session.query(_MlDataset).order_by(_MlDataset.created_at.desc()).all()
    return [{
        "id": r.id, "name": r.name, "market": r.market, "label": r.label,
        "factor_ids": _json.loads(r.factor_ids) if r.factor_ids else [],
        "train_range": f"{r.train_start},{r.train_end}",
        "val_range": f"{r.val_start},{r.val_end}",
        "test_range": f"{r.test_start},{r.test_end}",
        "bq_table": r.bq_table, "status": r.status, "row_count": r.row_count,
        "created_at": r.created_at.isoformat() if r.created_at else None,
    } for r in rows]


@app.get("/api/admin/ml/datasets/{market}/factors")
def admin_ml_dataset_factors(market: str):
    """Return available factor columns from BQ factor_values for a given market."""
    client = _bq.Client(project="deductive-notch-495015-c2")
    query = """
        SELECT DISTINCT factor_id, source_builder
        FROM quant.factor_values
        WHERE factor_id LIKE @prefix
        ORDER BY factor_id
    """
    prefix = f"{market}_%"
    job_config = bigquery.QueryJobConfig(
        query_parameters=[bigquery.ScalarQueryParameter("prefix", "STRING", prefix)]
    )
    try:
        rows = client.query(query, job_config=job_config).result()
        return [{
            "factor_id": r.factor_id,
            "source": r.source_builder or "unknown",
            "label": r.factor_id.replace(f"{market}_", "", 1),
        } for r in rows]
    except Exception:
        return []


@app.post("/api/admin/ml/datasets")
def admin_ml_dataset_create(body: dict = Body(...)):
    session = get_session()
    name = body.get("name", "")
    if not name:
        raise HTTPException(400, detail="Missing 'name'")
    if session.query(_MlDataset).filter(_MlDataset.name == name).first():
        raise HTTPException(409, detail=f"Dataset '{name}' already exists")
    ds = _MlDataset(
        name=name, market=body.get("market", "us"), label=body.get("label", "fwd_ret_5d"),
        factor_ids=_json.dumps(body.get("factor_ids", [])),
        train_start=body.get("train_start", ""), train_end=body.get("train_end", ""),
        val_start=body.get("val_start", ""), val_end=body.get("val_end", ""),
        test_start=body.get("test_start", ""), test_end=body.get("test_end", ""),
    )
    session.add(ds)
    session.commit()
    return {"id": ds.id, "name": ds.name, "status": ds.status}


@app.post("/api/admin/ml/datasets/{ds_id}/generate")
def admin_ml_dataset_generate(ds_id: int):
    """Build/replace BQ table for a dataset in ml_dataset.{name}. Logs to train module."""
    from datetime import datetime as _dt
    session = get_session()
    ds = session.query(_MlDataset).filter(_MlDataset.id == ds_id).first()
    if not ds:
        raise HTTPException(404, detail=f"Dataset {ds_id} not found")

    # Generate via task queue for logging
    cmd = (f"mkdir -p /var/log/quant/prod/train && cd /opt/quant && "
           f"PYTHONPATH=/opt/quant python3 -c \""
           f"from admin.server import _generate_dataset_inner; "
           f"_generate_dataset_inner({ds_id})\" "
           f"2>&1 | while IFS= read -r l; do echo \"$(date -u +%Y-%m-%dT%H:%M:%SZ) $l\"; done "
           f"| tee -a /var/log/quant/prod/train/dataset_{ds.name}.log")
    session = get_session()
    task = Task(type="shell", params={"cmd": cmd, "dataset": ds.name}, status="pending")
    session.add(task)
    session.commit()
    return {"task_id": task.id}


def _generate_dataset_inner(ds_id: int):
    """Inner function called by task worker. Logs to stdout which is piped to tee."""
    import json as _json
    from admin.models import get_session, MlDataset
    from google.cloud import bigquery as _bq_inner
    from datetime import datetime, timezone

    session = get_session()
    ds = session.query(MlDataset).filter(MlDataset.id == ds_id).first()
    if not ds:
        print(f"ERROR: dataset {ds_id} not found")
        return

    factor_ids = _json.loads(ds.factor_ids)
    if not factor_ids:
        print("ERROR: no factors selected")
        return

    print(f"Generating dataset {ds.name} — {len(factor_ids)} factors")
    client = _bq_inner.Client(project="deductive-notch-495015-c2")
    table_name = ds.name
    full_table = f"deductive-notch-495015-c2.ml_dataset.{table_name}"
    label_col = ds.label
    market_prefix = ds.market + "_"

    pivot_cols = ",\n                   ".join(
        "MAX(CASE WHEN factor_id = '{}' THEN value END) AS {}".format(
            f, f.replace(market_prefix, "", 1)
        )
        for f in factor_ids
    )

    try:
        client.query(f"DROP TABLE IF EXISTS deductive-notch-495015-c2.ml_dataset.{table_name}").result()
        print(f"Creating table deductive-notch-495015-c2.ml_dataset.{table_name}...")

        # Normalize symbol expression for CREATE TABLE
        if ds.market == "hk":
            norm_sym = f"LPAD(REGEXP_REPLACE(REPLACE(symbol, 'HK.', ''), r'^0+', ''), 5, '0') AS symbol"
        else:
            norm_sym = f"REPLACE(symbol, 'US.', '') AS symbol"

        create_sql = f"""
            CREATE TABLE deductive-notch-495015-c2.ml_dataset.{table_name} AS
            WITH raw AS (
                SELECT {norm_sym}, date, factor_id, value,
                       CASE
                           WHEN date BETWEEN '{ds.train_start}' AND '{ds.train_end}' THEN 'train'
                           WHEN date BETWEEN '{ds.val_start}' AND '{ds.val_end}' THEN 'val'
                           WHEN date BETWEEN '{ds.test_start}' AND '{ds.test_end}' THEN 'test'
                       END AS split
                FROM deductive-notch-495015-c2.quant.factor_values
                WHERE factor_id IN UNNEST(@factor_ids)
                  AND date BETWEEN '{ds.train_start}' AND '{ds.test_end}'
            )
            SELECT symbol, date, split,
                   {pivot_cols},
                   CAST(NULL AS FLOAT64) AS `{label_col}`
            FROM raw
            WHERE split IS NOT NULL
            GROUP BY symbol, date, split
        """
        job_config = _bq_inner.QueryJobConfig(
            query_parameters=[_bq_inner.ArrayQueryParameter("factor_ids", "STRING", factor_ids)]
        )
        client.query(create_sql, job_config=job_config).result()

        cnt = list(client.query(f"SELECT COUNT(*) AS n FROM {full_table}").result())[0].n
        print(f"Feature table created: {cnt} rows")

        # Compute forward-return label from bars data
        import re as _re
        fwd_match = _re.match(r"fwd_ret_(\d+)d", label_col)
        if fwd_match:
            n_days = int(fwd_match.group(1))
            bars_table = "us_bars_1d" if ds.market == "us" else "hk_bars_1d"
            bars_prefix = "US." if ds.market == "us" else "HK."
            # Extend end date to have forward-looking close prices
            from datetime import timedelta as _td
            test_end_dt = datetime.strptime(ds.test_end, "%Y-%m-%d").date() + _td(days=n_days + 14)
            bars_end = test_end_dt.strftime("%Y-%m-%d")
            print(f"Computing {label_col} ({n_days}-day forward return) from {bars_table} ({ds.train_start} to {bars_end})...")

            # Build normalized symbol expression: strip prefix + (HK) zero-pad to 5 digits
            if ds.market == "hk":
                norm_col = f"LPAD(REGEXP_REPLACE(REPLACE(symbol, '{bars_prefix}', ''), r'^0+', ''), 5, '0')"
            else:
                norm_col = f"REPLACE(symbol, '{bars_prefix}', '')"

            update_sql = f"""
                MERGE INTO `{full_table}` t
                USING (
                    SELECT symbol, date,
                           LEAD(close, {n_days}) OVER (PARTITION BY symbol ORDER BY date) / close - 1 AS fwd_ret
                    FROM (
                        SELECT
                            {norm_col} AS symbol,
                            DATE(timestamp) AS date,
                            ARRAY_AGG(close ORDER BY _ingest_time DESC LIMIT 1)[OFFSET(0)] AS close
                        FROM `deductive-notch-495015-c2.quant.{bars_table}`
                        WHERE DATE(timestamp) BETWEEN '{ds.train_start}' AND '{bars_end}'
                        GROUP BY {norm_col}, DATE(timestamp)
                    )
                ) fwd
                ON {norm_col.replace('symbol', 't.symbol')} = fwd.symbol
                   AND t.date = fwd.date
                WHEN MATCHED THEN UPDATE SET `{label_col}` = fwd.fwd_ret
            """
            client.query(update_sql).result()

            non_null = list(client.query(
                f"SELECT COUNTIF(`{label_col}` IS NOT NULL) AS n FROM `{full_table}`"
            ).result())[0].n
            print(f"Label computed: {non_null}/{cnt} non-null ({(non_null/cnt*100):.1f}%)")
        else:
            print(f"Label '{label_col}' is not a fwd_ret_Nd pattern, left as NULL")

        ds.bq_table = full_table
        ds.status = "ready"
        ds.row_count = cnt
        ds.updated_at = datetime.now(timezone.utc)
        session.commit()
    except Exception as e:
        print(f"ERROR: {e}")
        ds.status = "failed"
        session.commit()


@app.delete("/api/admin/ml/datasets/{ds_id}")
def admin_ml_dataset_delete(ds_id: int):
    session = get_session()
    ds = session.query(_MlDataset).filter(_MlDataset.id == ds_id).first()
    if not ds:
        raise HTTPException(404, detail=f"Dataset {ds_id} not found")
    if ds.bq_table:
        try:
            _bq.Client(project="deductive-notch-495015-c2").query(f"DROP TABLE IF EXISTS {ds.bq_table}").result()
        except Exception:
            pass
    session.delete(ds)
    session.commit()
    return {"status": "ok"}


# ── ML Configs ────────────────────────────────────────────────────────────────

@app.get("/api/admin/ml/configs")
def admin_ml_configs():
    session = get_session()
    rows = session.query(_MlConfig).order_by(_MlConfig.created_at.desc()).all()
    return [{
        "id": r.id, "name": r.name, "description": r.description,
        "config_path": r.config_path, "dataset_name": r.dataset_name,
        "registry_model_name": r.registry_model_name, "status": r.status,
        "created_at": r.created_at.isoformat() if r.created_at else None,
        "updated_at": r.updated_at.isoformat() if r.updated_at else None,
    } for r in rows]


@app.get("/api/admin/ml/configs/{name}")
def admin_ml_config_get(name: str):
    path = _ML_CONFIG_DIR / name if name.endswith(".yaml") else _ML_CONFIG_DIR / f"{name}.yaml"
    if not path.exists():
        raise HTTPException(404, detail=f"Config '{name}' not found")
    return {"name": name, "content": path.read_text()}


@app.put("/api/admin/ml/configs/{name}")
def admin_ml_config_put(name: str, body: dict = Body(...)):
    import shutil
    content = body.get("content", "")
    if not content:
        raise HTTPException(400, detail="Missing 'content'")
    _ML_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    fname = name if name.endswith(".yaml") else f"{name}.yaml"
    path = _ML_CONFIG_DIR / fname
    if path.exists():
        shutil.copy2(path, path.with_suffix(".yaml.bak"))
    path.write_text(content)
    # Parse YAML to extract dataset_name and registry_model_name
    try:
        cfg = _yaml.safe_load(content)
        dataset_name = cfg.get("data", {}).get("dataset", "")
        registry_name = cfg.get("registry", {}).get("model_name", "")
    except Exception:
        dataset_name = ""
        registry_name = ""
    # Upsert to DB
    session = get_session()
    existing = session.query(_MlConfig).filter(_MlConfig.name == fname).first()
    if existing:
        existing.config_path = str(path)
        existing.dataset_name = dataset_name
        existing.registry_model_name = registry_name
    else:
        session.add(_MlConfig(
            name=fname, config_path=str(path),
            dataset_name=dataset_name, registry_model_name=registry_name,
            description=body.get("description", ""),
        ))
    session.commit()
    return {"status": "ok", "name": fname}


@app.delete("/api/admin/ml/configs/{name}")
def admin_ml_config_delete(name: str):
    session = get_session()
    fname = name if name.endswith(".yaml") else f"{name}.yaml"
    cfg = session.query(_MlConfig).filter(_MlConfig.name == fname).first()
    if not cfg:
        raise HTTPException(404, detail=f"Config '{fname}' not found")
    # Check MLflow for registered models
    if cfg.registry_model_name:
        try:
            versions = ModelRegistry.list_versions(cfg.registry_model_name)
            if versions:
                raise HTTPException(409, detail=f"模型 '{cfg.registry_model_name}' 下有 {len(versions)} 个版本，请先在 MLflow 删除所有版本")
        except HTTPException:
            raise
        except Exception:
            pass
    path = _ML_CONFIG_DIR / fname
    if path.exists():
        import shutil
        shutil.move(str(path), str(path.with_suffix(".yaml.del")))
    session.delete(cfg)
    session.commit()
    return {"status": "ok"}


@app.post("/api/admin/ml/configs/{name}/rename")
def admin_ml_config_rename(name: str, body: dict = Body(...)):
    """Rename a ML config template. Updates DB record and file."""
    import shutil as _sh
    new_name = body.get("new_name", "").strip()
    if not new_name:
        raise HTTPException(400, detail="Missing 'new_name'")
    if not new_name.endswith(".yaml"):
        new_name += ".yaml"
    old_fname = name if name.endswith(".yaml") else f"{name}.yaml"
    new_fname = new_name if new_name.endswith(".yaml") else f"{new_name}.yaml"
    old_path = _ML_CONFIG_DIR / old_fname
    new_path = _ML_CONFIG_DIR / new_fname
    if not old_path.exists():
        raise HTTPException(404, detail=f"Config '{old_fname}' not found")
    if new_path.exists():
        raise HTTPException(409, detail=f"Config '{new_fname}' already exists")
    # Rename file
    _sh.move(str(old_path), str(new_path))
    # Update DB
    session = get_session()
    cfg = session.query(_MlConfig).filter(_MlConfig.name == old_fname).first()
    if cfg:
        cfg.name = new_fname
        cfg.config_path = str(new_path)
        session.commit()
    return {"status": "ok", "old_name": old_fname, "new_name": new_fname}


@app.post("/api/admin/ml/configs/{name}/register")
def admin_ml_config_register(name: str):
    """Register config to model center."""
    session = get_session()
    fname = name if name.endswith(".yaml") else f"{name}.yaml"
    cfg = session.query(_MlConfig).filter(_MlConfig.name == fname).first()
    if not cfg:
        raise HTTPException(404, detail=f"Config '{fname}' not found")
    cfg.status = "registered"
    session.commit()
    return {"status": "ok", "name": fname}


# ── Model Center ──────────────────────────────────────────────────────────────

@app.get("/api/admin/ml/center")
def admin_ml_center():
    """Model center list: MLflow models + config info."""
    session = get_session()
    # Load configs for dataset_name / config_name lookup
    config_map: dict[str, dict] = {}
    for cfg in session.query(_MlConfig).all():
        key = cfg.registry_model_name or cfg.name.replace(".yaml", "")
        config_map[key] = {"dataset_name": cfg.dataset_name or "", "config_name": cfg.name}

    # ── Models from MLflow (via unified entry) ──
    mlflow_models = ModelRegistry.list_all_models()

    result = []
    seen: set[str] = set()
    for m in mlflow_models:
        model_name = m["name"]
        if model_name in seen:
            continue
        seen.add(model_name)
        cfg = config_map.get(model_name, {})

        # 🆕 Auto-sync: if MLflow model exists but no ml_configs row, create one
        if not cfg:
            dataset_name = ""
            for v in m["versions"]:
                ds = v.get("dataset", "")
                if ds:
                    dataset_name = ds
                    break
            fname = f"{model_name}.yaml"
            existing = session.query(_MlConfig).filter(_MlConfig.name == fname).first()
            if not existing:
                session.add(_MlConfig(
                    name=fname,
                    config_path=str(_ML_CONFIG_DIR / fname),
                    dataset_name=dataset_name,
                    registry_model_name=model_name,
                    status="trained",
                ))
                session.commit()
            cfg = {"dataset_name": dataset_name, "config_name": fname}
        result.append({
            "model_name": model_name,
            "dataset_name": cfg.get("dataset_name", "—"),
            "config_name": cfg.get("config_name", "—"),
            "versions": [{
                "version": v["version"],
                "stage": v["stage"],
                "run_id": v["run_id"],
                "rmse": v.get("rmse"),
                "ic": v.get("rank_ic"),
                "icir": v.get("icir"),
                "n_features": v.get("n_features"),
                "dataset": v.get("dataset"),
                "training_time": v.get("training_time"),
                "created_at": v.get("created_at"),
                "completed_at": v.get("completed_at"),
            } for v in m["versions"]],
            "last_trained_at": m["versions"][0].get("created_at") if m["versions"] else None,
        })

    # ── Configs registered but not yet trained ──
    for cfg in session.query(_MlConfig).filter(_MlConfig.status == "registered").all():
        model_name = cfg.registry_model_name or cfg.name.replace(".yaml", "")
        if model_name not in seen:
            seen.add(model_name)
            result.append({
                "model_name": model_name,
                "dataset_name": cfg.dataset_name or "",
                "config_name": cfg.name,
                "versions": [],
                "last_trained_at": None,
            })
    return result


@app.post("/api/admin/ml/train")
def admin_ml_train(body: dict = Body(...)):
    """Submit training task for a config."""
    config_name = body.get("config_name", "")
    skip_tuning = body.get("skip_tuning", False)
    if not config_name:
        raise HTTPException(400, detail="Missing 'config_name'")
    fname = config_name if config_name.endswith(".yaml") else f"{config_name}.yaml"
    path = _ML_CONFIG_DIR / fname
    if not path.exists():
        raise HTTPException(404, detail=f"Config '{config_name}' not found")
    # Read model_name from config YAML for log file naming
    import yaml as _yaml
    model_name = config_name.replace(".yaml", "")
    try:
        cfg = _yaml.safe_load(path.read_text()) or {}
        registry_name = cfg.get("registry", {}).get("model_name", "")
        if registry_name:
            model_name = registry_name
    except Exception:
        pass
    cmd = (f"mkdir -p /var/log/quant/prod/train && cd /opt/quant && PYTHONPATH=/opt/quant "
           f"python3 -c \"import logging; logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s [%(name)s] %(message)s'); "
           f"from ml.pipeline import TrainPipeline; "
           f"p = TrainPipeline('{path}'); p.run(skip_tuning={skip_tuning})\" "
           f"2>&1 | while IFS= read -r l; do echo \"$(date -u +%Y-%m-%dT%H:%M:%SZ) $l\"; done "
           f"| tee -a /var/log/quant/prod/train/{model_name}_$(date -u +%Y%m%d_%H%M%S).log")
    session = get_session()
    task = Task(type="shell", params={"cmd": cmd, "config": config_name}, status="pending")
    session.add(task)
    session.commit()
    return {"task_id": task.id}


@app.delete("/api/admin/ml/center/{model_name}")
def admin_ml_center_delete(model_name: str):
    """Unregister from model center. Blocks if MLflow has trained versions."""
    # Check MLflow first — don't allow unregister if trained models exist
    try:
        mlflow_versions = ModelRegistry.list_versions(model_name)
        if mlflow_versions:
            raise HTTPException(
                409,
                detail=f"模型 '{model_name}' 在 MLflow 中有 {len(mlflow_versions)} 个版本。请先在 MLflow 删除所有版本后再取消注册。"
            )
    except HTTPException:
        raise
    except Exception:
        pass  # MLflow unreachable — allow unregister anyway

    session = get_session()
    configs = session.query(_MlConfig).filter(
        (_MlConfig.registry_model_name == model_name) |
        (_MlConfig.name == model_name + ".yaml")
    ).all()
    for c in configs:
        if c.status == "registered":
            c.status = "draft"
    session.commit()
    return {"status": "ok", "unregistered": len(configs)}


# ── MLflow Proxy ─────────────────────────────────────────────────────────
# MLflow runs on :5000 but cloudflared only tunnels :8091.
# Proxy requests so the embedded iframe works through the tunnel.

import httpx
from fastapi.responses import StreamingResponse

_MLFLOW_BASE = "http://127.0.0.1:5000"


@app.get("/mlflow")
async def mlflow_redirect():
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url="/mlflow/")


@app.api_route("/mlflow/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
async def mlflow_proxy(path: str, request: Request):
    """Reverse proxy to MLflow server."""
    url = f"{_MLFLOW_BASE}/{path}"
    async with httpx.AsyncClient(timeout=30) as client:
        body = await request.body()
        headers = {k: v for k, v in request.headers.items() if k.lower() not in ("host", "content-length")}
        r = await client.request(request.method, url, content=body, headers=headers, params=request.query_params)
        return StreamingResponse(
            iter([r.content]),
            status_code=r.status_code,
            headers={k: v for k, v in r.headers.items() if k.lower() not in ("content-encoding", "transfer-encoding", "content-length")},
        )


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
    # 1. Query BQ for experiments with equity data
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
    equity_map: dict[str, dict] = {}
    try:
        rows = client.query(query).result()
        for row in rows:
            equity_map[row.exp_id] = {
                "exp_id": row.exp_id, "ts": _db_serialize(row.ts),
                "bar": row.bar, "equity": row.equity, "cash": row.cash,
                "portfolio_value": row.portfolio_value, "daily_pnl": row.daily_pnl,
                "drawdown": row.drawdown,
            }
    except Exception as exc:
        logging.getLogger(__name__).error("dash_experiments query error: %s", exc)

    # 2. Also include registry experiments that have no equity yet
    from live.experiment_manager import ExperimentManager
    mgr = ExperimentManager()
    for exp in mgr.list(exp_type=type or None):
        if "test" in exp.id:
            continue
        if exp.id not in equity_map:
            equity_map[exp.id] = {
                "exp_id": exp.id, "ts": exp.created_at,
                "bar": 0, "equity": 0, "cash": 0,
                "portfolio_value": 0, "daily_pnl": 0, "drawdown": 0,
            }
    return sorted(equity_map.values(), key=lambda x: x["ts"], reverse=True)


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
                from common.normalize import normalize_symbol
                d["symbol"] = normalize_symbol(d["symbol"], "hk")
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
        from common.normalize import normalize_symbol, queryize_symbol
        bq_sym = queryize_symbol(bare, market)
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
    """Return run history from registry (SSOT, synced with Lab)."""
    from live.experiment_manager import ExperimentManager
    mgr = ExperimentManager()
    try:
        mgr.auto_heal(exp_id)
        runs = mgr.runs(exp_id)
        return [{
            "run_id": r.run_id,
            "status": r.status,
            "started_at": r.started_at,
            "ended_at": r.ended_at,
            "base_run": r.base_run,
        } for r in runs]
    except KeyError:
        return []


# ── Paper Run Dashboard API ──

@app.get("/api/admin/dashboard/paper-runs")
async def dash_paper_runs(limit: int = 50):
    """List paper runs: BQ completed results + registry active experiments."""
    results = []
    
    # 1. Active paper experiments from registry (source of truth)
    try:
        from live.experiment_manager import ExperimentManager
        mgr = ExperimentManager()
        for exp in mgr.list(exp_type="paper"):
            if exp.has_active_run and exp.active_run:
                results.append({
                    "run_id": exp.active_run.run_id,
                    "name": exp.name or exp.id,
                    "strategy": exp.strategy,
                    "market": exp.market,
                    "status": "running",
                    "n_periods": 0,
                    "created_at": exp.active_run.started_at,
                    "error_msg": None,
                    "_source": "registry",
                })
    except Exception as e:
        logging.getLogger(__name__).warning("paper-runs registry query: %s", e)

    # 2. Completed paper runs from BQ
    try:
        client = _DB_BQ()
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
        for r in rows:
            d = _db_row_to_dict(r, names)
            d["_source"] = "bq"
            # Don't duplicate registry runs
            if not any(e["run_id"] == d["run_id"] for e in results):
                results.append(d)
    except Exception as e:
        logging.getLogger(__name__).error("dash_paper_runs query failed: %s", e)

    return results


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
                        from common.normalize import normalize_symbol
                        t["symbol"] = normalize_symbol(t["symbol"], "hk")
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
            WHERE timestamp >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 72 HOUR)
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
            WHERE timestamp >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 72 HOUR)
        """
        rows = list(client.query(q).result())
        if rows and rows[0].latest:
            result["hk"] = _db_serialize(rows[0].latest)
    except Exception as exc:
        logging.getLogger(__name__).error("dash_pipeline hk query error: %s", exc)
    try:
        # HK index pipeline
        q = f"""
            SELECT MAX(timestamp) AS latest
            FROM {_DB_TABLE("hk_bars_index_5m")}
            WHERE timestamp >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 72 HOUR)
        """
        rows = list(client.query(q).result())
        if rows and rows[0].latest:
            result["hk_index"] = _db_serialize(rows[0].latest)
    except Exception as exc:
        logging.getLogger(__name__).error("dash_pipeline hk_index query error: %s", exc)
    try:
        # US index pipeline
        q = f"""
            SELECT MAX(timestamp) AS latest
            FROM {_DB_TABLE("us_bars_index_5m")}
            WHERE timestamp >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 72 HOUR)
        """
        rows = list(client.query(q).result())
        if rows and rows[0].latest:
            result["us_index"] = _db_serialize(rows[0].latest)
    except Exception as exc:
        logging.getLogger(__name__).error("dash_pipeline us_index query error: %s", exc)
    return result


def _load_symbols_config():
    """Load symbols.yaml once per request (FastAPI module-level cache)."""
    import yaml as _y
    _quant_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    config_path = Path(_quant_root) / "config/symbols.yaml"
    return _y.safe_load(config_path.read_text())


# ── Market Data API ──

@app.get("/api/admin/dashboard/market/symbols/{market}")
async def dash_market_symbols(market: str):
    import yaml
    from pathlib import Path
    import os as _os
    _quant_root = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
    config_path = Path(_quant_root) / "config/symbols.yaml"
    cfg = yaml.safe_load(config_path.read_text())
    syms = cfg.get("markets", {}).get(market, {}).get("symbols", [])
    prefix = f"{'US' if market == 'us' else 'HK'}."
    return [s.replace(prefix, "") for s in syms if s.startswith(prefix)]


@app.get("/api/admin/dashboard/market/{market}/{symbol}")
async def dash_market_bars(market: str, symbol: str, limit: int = 78, days: int = 2):
    client = _DB_BQ()
    # Detect index symbols → route to _bars_index_5m table
    cfg = _load_symbols_config()
    index_syms = cfg.get("indices", {}).get(market, {}).get("symbols", [])
    is_index = symbol in index_syms or (market == "us" and symbol.startswith("^"))
    table = _DB_TABLE(f"{market}_bars_index_5m" if is_index else f"{market}_bars_5m")
    full_symbol = symbol if is_index else f"{'US' if market == 'us' else 'HK'}.{symbol}"
    if market == "hk":
        ts_expr = "TIMESTAMP_SUB(timestamp, INTERVAL 8 HOUR)"
    else:
        ts_expr = 'TIMESTAMP(DATETIME(timestamp), "America/New_York")'
    # Use requested days + 2 extra to handle weekends/holidays
    window_days = max(days, 2) + 2
    query = f"""
        WITH dedup AS (
          SELECT {ts_expr} AS timestamp, open, high, low, close, volume,
            ROW_NUMBER() OVER (PARTITION BY symbol, timestamp ORDER BY _ingest_time DESC NULLS LAST) AS rn
          FROM `{table}`
          WHERE symbol = '{full_symbol}'
            AND {ts_expr} >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL {window_days} DAY)
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


# ── Static + SPA fallback (production build, after all API routes) ────────────

import os as _os
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

DIST = _os.path.join(_os.path.dirname(__file__), "frontend", "dist")

if _os.path.isdir(DIST):
    # Serve built assets under /assets/
    assets_dir = _os.path.join(DIST, "assets")
    if _os.path.isdir(assets_dir):
        app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")

    @app.get("/{full_path:path}")
    async def spa_fallback(full_path: str):
        """SPA fallback: serve file if exists in dist/, else index.html for React Router."""
        file_path = _os.path.join(DIST, full_path)
        if _os.path.isfile(file_path):
            return FileResponse(file_path)
        return FileResponse(_os.path.join(DIST, "index.html"))


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("admin.server:app", host="0.0.0.0", port=8091, reload=True)
