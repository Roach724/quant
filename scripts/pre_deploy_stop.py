#!/usr/bin/env python3
"""Pre-deploy graceful shutdown: stop experiments + mark runs as stopped.

Called by CD workflow before docker compose up -d.
"""
import json
import os
import signal
from datetime import datetime, timezone

REGISTRY = "/var/quant/experiments/registry.json"
PID_DIR = "/var/quant/state"


def main():
    # 1. Stop all running experiments
    if not os.path.exists(REGISTRY):
        return

    with open(REGISTRY) as f:
        data = json.load(f)

    now = datetime.now(timezone.utc).isoformat()
    stopped = 0

    for exp_id, entry in data.get("experiments", {}).items():
        pid_file = os.path.join(PID_DIR, f"{exp_id}.pid")
        pid = None
        if os.path.exists(pid_file):
            try:
                with open(pid_file) as pf:
                    pid = int(pf.read().strip())
            except (ValueError, OSError):
                pass

        if pid:
            try:
                os.killpg(os.getpgid(pid), signal.SIGTERM)
                # Wait up to 15s for graceful exit
                for _ in range(30):
                    try:
                        os.kill(pid, 0)
                    except OSError:
                        break
                    import time
                    time.sleep(0.5)
                else:
                    os.killpg(os.getpgid(pid), signal.SIGKILL)
                print(f"Stopped {exp_id} (PID {pid})")
            except (ProcessLookupError, OSError):
                pass
            try:
                os.remove(pid_file)
            except OSError:
                pass

        # Mark running runs as stopped
        for run in entry.get("runs", []):
            if run.get("status") == "running" and not run.get("ended_at"):
                run["status"] = "stopped"
                run["ended_at"] = now
                stopped += 1
        entry["current_run"] = None
        entry["pid"] = None

    if stopped:
        with open(REGISTRY, "w") as f:
            json.dump(data, f, indent=2)
        print(f"Marked {stopped} runs as stopped")

    # 2. Stop ws_collector
    import subprocess
    subprocess.run(
        ["supervisorctl", "stop", "ws_collector"],
        capture_output=True, timeout=10,
    )
    print("ws_collector stopped")

    # 3. Stop trading strategies
    import glob
    import time

    trading_pid_dirs = ["/var/data/trading/sim/pids", "/var/data/trading/real/pids"]
    for pid_dir in trading_pid_dirs:
        if not os.path.isdir(pid_dir):
            continue
        for pid_file in sorted(glob.glob(f"{pid_dir}/strategy_*.pid")):
            pid = None
            try:
                with open(pid_file) as pf:
                    pid = int(pf.read().strip())
            except (ValueError, OSError):
                continue

            if pid:
                try:
                    os.kill(pid, signal.SIGTERM)
                    for _ in range(30):
                        try:
                            os.kill(pid, 0)
                        except OSError:
                            break
                        time.sleep(0.5)
                    else:
                        os.kill(pid, signal.SIGKILL)
                    print(f"Stopped trading strategy {os.path.basename(pid_file)} (PID {pid})")
                except (ProcessLookupError, OSError):
                    pass
                try:
                    os.remove(pid_file)
                except OSError:
                    pass

    # 4. Mark trading strategies as stopped in DB
    try:
        import sqlite3

        for env in ["sim", "real"]:
            db_path = f"/var/data/trading/{env}/trading.db"
            if not os.path.exists(db_path):
                continue
            conn = sqlite3.connect(db_path)
            conn.execute(
                "UPDATE trading_strategies SET status='stopped' WHERE status='running'"
            )
            conn.commit()
            conn.close()
        print("Trading strategies marked as stopped in DB")
    except Exception as e:
        print(f"WARNING: failed to update trading DB: {e}")


if __name__ == "__main__":
    main()
