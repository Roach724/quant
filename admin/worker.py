"""Quant Admin Platform — background task worker.

Polls the Task table for status="pending" rows, executes the shell command
via subprocess, and updates the row to "done" / "failed".
"""

import subprocess
import time
from datetime import datetime

from admin.models import get_session, Task

PROJECT_ROOT = "/opt/quant-prod"
POLL_INTERVAL = 2  # seconds


def run_one(task: Task) -> None:
    """Execute a single pending task."""
    session = get_session()
    try:
        # Re-fetch inside a fresh session so we don't race
        t = session.get(Task, task.id)
        if t is None or t.status != "pending":
            return

        t.status = "running"
        t.started_at = datetime.utcnow()
        session.commit()

        command = t.params.get("command", "echo no command") if t.params else "echo no command"
        proc = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=300,
            cwd=PROJECT_ROOT,
        )

        t.status = "done" if proc.returncode == 0 else "failed"
        t.result = proc.stdout.strip() or proc.stderr.strip()
        t.finished_at = datetime.utcnow()
        session.commit()

    except subprocess.TimeoutExpired:
        t.status = "failed"
        t.result = "Timeout after 300s"
        t.finished_at = datetime.utcnow()
        session.commit()
    except Exception as exc:
        t.status = "failed"
        t.result = str(exc)
        t.finished_at = datetime.utcnow()
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
