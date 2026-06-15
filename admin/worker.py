"""Quant Admin Platform — background task worker.

Polls the Task table for status="pending" rows, executes the shell command
via subprocess, and updates the row to "done" / "failed".
"""

import subprocess
import time
import os
import glob
from datetime import datetime, timezone

from admin.models import get_session, Task, CronRun

PROJECT_ROOT = "/opt/quant"
POLL_INTERVAL = 2  # seconds
# All cron log subdirectories to search for log files
_LOG_DIRS = ["/var/log/quant/prod/cron", "/var/log/quant/prod/factor", "/var/log/quant/prod/quality", "/var/log/quant/prod/loader"]


def _handle_trading_start(task, env: str) -> None:
    """Launch a trading strategy as a detached subprocess."""
    import subprocess as _sp
    import os as _os
    from pathlib import Path

    params = task.params or {}
    if isinstance(params, str):
        import json as _json
        params = _json.loads(params)

    strategy_id = params.get("strategy_id")
    task.result = f"Launching trading_{env} strategy #{strategy_id}"

    cmd = (
        f"cd /opt/quant && PYTHONPATH=/opt/quant "
        f"python3 trading/run.py --strategy-id {strategy_id} --env {env.replace('trading_', '')}"
    )

    # Detached subprocess (no wait — runs until stopped)
    proc = _sp.Popen(
        cmd,
        shell=True,
        stdout=_sp.DEVNULL,
        stderr=_sp.DEVNULL,
        start_new_session=True,
    )

    # Give it a moment to write the PID file, then verify
    import time
    time.sleep(2)
    pid_dir = Path(f"/var/data/trading/{env.replace('trading_', '')}/pids")
    pid_file = pid_dir / f"strategy_{strategy_id}.pid"
    if pid_file.exists():
        task.result = f"Launched trading_{env} strategy #{strategy_id} (PID {proc.pid})"
    else:
        task.result = f"Launched trading_{env} strategy #{strategy_id} (PID {proc.pid}) — PID file check pending"


def _handle_ai_decision_run(task) -> None:
    """Execute the AI Decision Engine pipeline for a pending task."""
    import sys, json
    from pathlib import Path

    # Add project root to path
    sys.path.insert(0, PROJECT_ROOT)

    params = task.params or {}
    if isinstance(params, str):
        params = json.loads(params)

    run_id = params.get("run_id")
    strategy_name = params.get("strategy_name", "unknown")
    market = params.get("market", "us")
    config_yaml = params.get("config_yaml", "")

    try:
        import yaml
        from ai_decision.config import AIDecisionConfig
        from ai_decision.engine import AIDecisionEngine
        from ai_decision.schemas import PortfolioDecision
        from admin.models import get_session, AiDecisionRun

        config_dict = yaml.safe_load(config_yaml)
        if config_dict is None:
            raise ValueError("empty config_yaml")

        # Ensure market override
        if "ai_decision" not in config_dict:
            config_dict["ai_decision"] = {}
        config_dict["ai_decision"]["market"] = market

        cfg = AIDecisionConfig(config_dict)
        engine = AIDecisionEngine(cfg)

        # Run full pipeline (async engine)
        import asyncio
        portfolio_plan: PortfolioDecision = asyncio.run(engine.run())

        # Persist results using Pydantic model's dict
        session = get_session()
        _summary = {}
        try:
            run = session.query(AiDecisionRun).filter(AiDecisionRun.id == run_id).first()
            if run:
                run.status = "success"
                # Convert intermediate engine state to JSON-safe dicts for SQLite
                def _safe(obj):
                    """Recursively convert non-serializable objects to strings."""
                    import datetime as _dt
                    if isinstance(obj, _dt.datetime):
                        return obj.isoformat()
                    if isinstance(obj, dict):
                        return {k: _safe(v) for k, v in obj.items()}
                    if isinstance(obj, (list, tuple)):
                        return [_safe(i) for i in obj]
                    return obj

                candidates = [_safe(c.model_dump()) for c in (engine.candidates or [])]
                reports = [_safe(r.model_dump()) for r in (engine.reports or [])]
                fusion = [_safe(f.model_dump()) for f in (engine.fusion_results or [])]
                decisions = _safe(portfolio_plan.model_dump()) if portfolio_plan else {}

                run.recall_result = candidates
                run.analysis_result = reports
                run.fusion_result = fusion
                run.decision_result = decisions
                run.summary = {
                    "symbols_screened": len(candidates),
                    "symbols_analyzed": len(reports),
                    "symbols_ranked": len(fusion),
                    "action": decisions.get("summary", {}).get("action", "?"),
                }
                _summary = run.summary
                run.finished_at = datetime.now(timezone.utc)
                session.commit()
        finally:
            session.close()

        task.result = json.dumps({"status": "success", "summary": _summary})

    except Exception as exc:
        import traceback
        tb = traceback.format_exc()
        task.result = str(exc)
        # Update run record
        try:
            from admin.models import get_session, AiDecisionRun
            session = get_session()
            try:
                run = session.query(AiDecisionRun).filter(AiDecisionRun.id == run_id).first()
                if run:
                    run.status = "failed"
                    run.error = f"{exc}\n{tb}"
                    run.finished_at = datetime.now(timezone.utc)
                    session.commit()
            finally:
                session.close()
        except Exception:
            pass
        raise


def run_one(task: Task) -> None:
    """Execute a single pending task."""
    session = get_session()
    try:
        # Re-fetch inside a fresh session so we don't race
        t = session.get(Task, task.id)
        if t is None or t.status != "pending":
            return

        t.status = "running"
        t.started_at = datetime.now(timezone.utc)
        session.commit()

        task_type = (t.params or {}).get("type", "") or t.type
        if task_type == "ai_decision_run":
            _handle_ai_decision_run(t)
            t.status = "completed"
            t.finished_at = datetime.now(timezone.utc)
            session.commit()
            return

        if task_type in ("trading_sim", "trading_prod"):
            _handle_trading_start(t, task_type)
            t.status = "completed"
            t.finished_at = datetime.now(timezone.utc)
            session.commit()
            return

        command = (t.params or {}).get("cmd") or (t.params or {}).get("command", "echo no command")

        # Create CronRun entry for manual triggers BEFORE running (so log link works during execution)
        cron_name = (t.params or {}).get("cron_name", "")
        cron_trigger = (t.params or {}).get("cron_trigger", "")
        cron_run_id = None
        if cron_name and cron_trigger == "manual":
            try:
                run = CronRun(
                    job_name=cron_name,
                    command=(t.params or {}).get("cron_command", ""),
                    trigger_type="manual",
                    status="running",
                    started_at=datetime.now(timezone.utc),
                )
                session.add(run)
                session.commit()
                cron_run_id = run.id
            except Exception:
                pass

        proc = subprocess.Popen(
            command,
            shell=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=PROJECT_ROOT,
            start_new_session=True,
        )
        stdout, stderr = proc.communicate(timeout=7200)

        t.status = "completed" if proc.returncode == 0 else "failed"
        t.result = stdout.strip() or stderr.strip()
        t.finished_at = datetime.now(timezone.utc)
        session.commit()

        # Update CronRun entry for manual triggers with final status + actual log file
        if cron_name and cron_trigger == "manual":
            try:
                # Find the pre-created record (by id) or query by name+running
                existing = None
                if cron_run_id:
                    existing = session.get(CronRun, cron_run_id)
                if existing is None:
                    from datetime import datetime as _dt
                    started_str = (t.params or {}).get("cron_started", "")
                    started_at = _dt.fromisoformat(started_str) if started_str else t.started_at
                    existing = CronRun(
                        job_name=cron_name,
                        command=(t.params or {}).get("cron_command", ""),
                        trigger_type="manual",
                        started_at=started_at or datetime.now(timezone.utc),
                    )
                    session.add(existing)
                # Find actual log file across all log directories
                try:
                    for log_dir in _LOG_DIRS:
                        if not os.path.isdir(log_dir):
                            continue
                        pattern = os.path.join(log_dir, f"{cron_name}_*.log")
                        candidates = sorted(glob.glob(pattern), key=os.path.getmtime, reverse=True)
                        if candidates:
                            existing.log_file = candidates[0]
                            break
                except Exception:
                    pass
                existing.status = "success" if proc.returncode == 0 else "failed"
                existing.exit_code = proc.returncode
                existing.finished_at = datetime.now(timezone.utc)
                existing.error_tail = (stderr or "")[:500] if proc.returncode != 0 else None
                session.commit()
            except Exception:
                pass

    except subprocess.TimeoutExpired:
        t.status = "failed"
        t.result = "Timeout after 300s"
        t.finished_at = datetime.now(timezone.utc)
        session.commit()
    except Exception as exc:
        t.status = "failed"
        t.result = str(exc)
        t.finished_at = datetime.now(timezone.utc)
        session.commit()
    finally:
        session.close()


def main():
    print(f"[worker] Starting …  PROJECT_ROOT={PROJECT_ROOT}")
    while True:
        session = get_session()
        try:
            pending = session.query(Task).filter(Task.status == "pending").order_by(Task.created_at).limit(1).all()
        finally:
            session.close()

        if pending:
            run_one(pending[0])
        else:
            time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
