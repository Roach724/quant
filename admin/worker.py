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
