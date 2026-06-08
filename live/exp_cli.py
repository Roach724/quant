#!/usr/bin/env python3
"""Experiment CLI — register, manage, and inspect experiments.

Usage:
    python live/exp_cli.py register live/us/ml/2 --config path/to/config.yaml
    python live/exp_cli.py start    live_us_ml_v2
    python live/exp_cli.py stop     live_us_ml_v2
    python live/exp_cli.py list  [--type live] [--status running]
    python live/exp_cli.py show     live_us_ml_v2
    python live/exp_cli.py runs     live_us_ml_v2

Process supervision:
    Live runs are wrapped in systemd transient units (systemd-run)
    so they survive crashes and node reboots (Restart=on-failure).
    Units are named: exp-{exp_id}  (e.g. exp-live_us_mom)
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from typing import Sequence

from live.experiment_manager import ExperimentManager


# ── ID parsing ───────────────────────────────────────────────────────

def _parse_register_id(raw: str) -> dict[str, str | int]:
    """Parse a slash-separated register spec into components.

    Accepted forms:
        type/market/strategy/version   e.g.  live/us/ml/2
        exp_id                         e.g.  live_us_ml_v2  (fallback parse)

    Returns dict with keys: exp_type, market, strategy, version (int).
    """
    # Try slash-separated first
    parts = raw.split("/")
    if len(parts) == 4:
        exp_type, market, strategy, ver_str = parts
        try:
            version = int(ver_str)
        except ValueError:
            raise ValueError(
                f"Version must be an integer, got '{ver_str}'"
            )
        return {"exp_type": exp_type, "market": market, "strategy": strategy, "version": version}

    # Try parsing as canonical id: {type}_{market}_{strategy}_v{version}
    if raw.count("_") >= 2:
        # Split on last _v for version
        if "_v" in raw:
            prefix, ver_str = raw.rsplit("_v", 1)
            try:
                version = int(ver_str)
            except ValueError:
                raise ValueError(
                    f"Cannot parse experiment id '{raw}'. "
                    f"Use form 'type/market/strategy/version' or canonical id."
                )
            # prefix is {type}_{market}_{strategy}
            prefix_parts = prefix.split("_", 2)
            if len(prefix_parts) == 3:
                exp_type, market, strategy = prefix_parts
                return {"exp_type": exp_type, "market": market, "strategy": strategy, "version": version}

    raise ValueError(
        f"Cannot parse experiment id '{raw}'. "
        f"Use form 'type/market/strategy/version' or canonical id like 'live_us_ml_v2'."
    )


# ── Command helpers ─────────────────────────────────────────────────

SYSTEMD_UNIT_PREFIX = "exp-"


def _systemd_unit_name(exp_id: str) -> str:
    """Generate systemd transient unit name for an experiment."""
    return f"{SYSTEMD_UNIT_PREFIX}{exp_id}"


def _is_unit_active(exp_id: str) -> bool:
    """Check if the systemd unit for an experiment is active."""
    unit = _systemd_unit_name(exp_id)
    try:
        result = subprocess.run(
            ["systemctl", "is-active", "--quiet", unit],
            capture_output=True, timeout=5,
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, OSError):
        return False


def _unit_pid(exp_id: str) -> int | None:
    """Get the main PID of a systemd unit."""
    unit = _systemd_unit_name(exp_id)
    try:
        result = subprocess.run(
            ["systemctl", "show", "--property=MainPID", "--value", unit],
            capture_output=True, text=True, timeout=5,
        )
        pid = int(result.stdout.strip())
        return pid if pid > 0 else None
    except (ValueError, subprocess.TimeoutExpired, OSError):
        return None


def cmd_start(mgr: ExperimentManager, args: argparse.Namespace) -> None:
    """Start an experiment: create run record, launch via systemd-run.

    Supports --resume-run to restart an existing run from its saved state
    without creating a new run record.
    """
    exp_id = args.id
    try:
        exp = mgr.get(exp_id)
    except KeyError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    if _is_unit_active(exp_id):
        print(f"Error: {exp_id} systemd unit is already active", file=sys.stderr)
        sys.exit(1)

    config_path = exp.config_path
    if not config_path:
        print(f"Error: no config_path for {exp_id}", file=sys.stderr)
        sys.exit(1)

    # Determine run_id: either resume existing or create new
    if args.resume_run:
        run_id = args.resume_run
        # Verify run exists in registry
        run_found = any(r.run_id == run_id for r in exp.runs)
        if not run_found:
            print(f"Error: run {run_id} not found for {exp_id}", file=sys.stderr)
            sys.exit(1)
        # Ensure experiment status is idle before launch
        if exp.status != "idle":
            mgr._data[exp_id]["status"] = "idle"
            mgr._save()
        print(f"Resuming run {run_id} for {exp_id}")
    else:
        if exp.has_active_run:
            active = exp.active_run
            print(f"Error: {exp_id} already has an active run ({active.run_id if active else '?'})",
                  file=sys.stderr)
            sys.exit(1)
        if exp.status != "idle":
            mgr._data[exp_id]["status"] = "idle"
            mgr._save()
        run_id = mgr.start(exp_id)
        print(f"Created run {run_id} for {exp_id}")

    # Launch experiment directly (Docker: no systemd)
    project_root = os.environ.get("QUANT_ROOT", "/opt/quant")
    python_bin = "python3"
    run_cmd = [
        python_bin, f"{project_root}/live/run.py",
        "--config", config_path,
        "--run-id", run_id,
    ]
    pid_file = f"/var/quant/state/{exp_id}.pid"
    log_file = f"/var/log/quant/live/{exp_id}_{run_id}.log"
    os.makedirs(os.path.dirname(log_file), exist_ok=True)

    try:
        with open(log_file, "a") as log_fh:
            proc = subprocess.Popen(
                run_cmd,
                stdout=log_fh, stderr=subprocess.STDOUT,
                cwd=project_root,
                start_new_session=True,
            )
        with open(pid_file, "w") as pf:
            pf.write(str(proc.pid))
        mgr.set_pid(exp_id, proc.pid)
        print(f"Started {exp_id} run {run_id} (PID={proc.pid})")
    except (subprocess.CalledProcessError, OSError) as e:
        if not args.resume_run:
            mgr.stop_run(exp_id, run_id)
        print(f"Error: launch failed: {e}", file=sys.stderr)
        sys.exit(1)


def cmd_stop(mgr: ExperimentManager, args: argparse.Namespace) -> None:
    """Stop the current active run via systemctl."""
    exp_id = args.id
    try:
        exp = mgr.get(exp_id)
    except KeyError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    if not exp.has_active_run:
        print(f"No active run for {exp_id}", file=sys.stderr)
        sys.exit(1)

    active = exp.active_run
    assert active is not None

    # Stop via PID file (Docker: no systemd)
    import signal
    pid_file = f"/var/quant/state/{exp_id}.pid"
    if os.path.exists(pid_file):
        try:
            with open(pid_file) as pf:
                pid = int(pf.read().strip())
            os.killpg(os.getpgid(pid), signal.SIGTERM)
            try:
                import time
                for _ in range(30):
                    try:
                        os.kill(pid, 0)
                        time.sleep(0.5)
                    except OSError:
                        break
                else:
                    os.killpg(os.getpgid(pid), signal.SIGKILL)
            except (ProcessLookupError, OSError):
                pass
            print(f"Stopped {exp_id} (PID={pid})")
        except (ValueError, ProcessLookupError, OSError) as e:
            print(f"Stop error: {e}")
        os.remove(pid_file)
    else:
        pid = mgr.get_pid(exp_id)
        if pid and _is_pid_alive(pid):
            os.killpg(os.getpgid(pid), signal.SIGTERM)
            print(f"Stopped {exp_id} via registry PID={pid}")

    mgr.set_pid(exp_id, None)
    mgr.stop_run(exp_id, active.run_id)
    print(f"Stopped {exp_id} run {active.run_id}")


def cmd_restart(mgr: ExperimentManager, args: argparse.Namespace) -> None:
    """Restart an experiment."""
    exp_id = args.id
    try:
        mgr.get(exp_id)
    except KeyError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    # Stop then start
    cmd_stop(mgr, args)
    # Need a new args with resume_run set
    class ResumeArgs:
        def __init__(self):
            self.id = exp_id
            self.resume_run = ""
    cmd_start(mgr, ResumeArgs())
    print(f"Restarted {exp_id}")

def cmd_register(mgr: ExperimentManager, args: argparse.Namespace) -> None:
    """Handle the 'register' subcommand."""
    spec = _parse_register_id(args.id)
    if not args.config:
        print("Error: --config is required for register", file=sys.stderr)
        sys.exit(1)
    try:
        exp_id = mgr.register(
            exp_type=spec["exp_type"],
            market=spec["market"],
            strategy=spec["strategy"],
            version=spec["version"],
            config_path=args.config,
            name=args.name or "",
        )
        exp = mgr.get(exp_id)
        print(f"Registered: {exp_id} ({exp.status})")
    except (ValueError, KeyError) as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


def cmd_pause(mgr: ExperimentManager, args: argparse.Namespace) -> None:
    """Handle the 'pause' subcommand."""
    exp_id = args.id
    try:
        mgr.pause(exp_id)
        print(f"Paused {exp_id}")
    except (RuntimeError, KeyError) as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


def cmd_resume(mgr: ExperimentManager, args: argparse.Namespace) -> None:
    """Handle the 'resume' subcommand."""
    exp_id = args.id
    try:
        run_id = mgr.resume(exp_id)
        print(f"Resumed {exp_id} → run {run_id}")
    except (RuntimeError, KeyError) as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)



def cmd_archive(mgr: ExperimentManager, args: argparse.Namespace) -> None:
    """Handle the 'archive' subcommand."""
    exp_id = args.id
    try:
        mgr.archive(exp_id)
        print(f"Archived {exp_id}")
    except (RuntimeError, KeyError) as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


def cmd_list(mgr: ExperimentManager, args: argparse.Namespace) -> None:
    """Handle the 'list' subcommand — table output."""
    try:
        experiments = mgr.list(exp_type=args.type, status=args.status)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    if not experiments:
        print("(no experiments)")
        return

    # Column widths
    header = f"{'[status]':<12} {'id':<35} {'run':<28}"
    print(header)
    print("-" * len(header))
    for exp in experiments:
        status_bracket = f"[{exp.status}]"
        run_str = exp.current_run or "-"
        print(f"{status_bracket:<12} {exp.id:<35} {run_str}")


def cmd_show(mgr: ExperimentManager, args: argparse.Namespace) -> None:
    """Handle the 'show' subcommand — JSON output."""
    exp_id = args.id
    try:
        exp = mgr.get(exp_id)
    except KeyError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    result = {
        "id": exp.id,
        "type": exp.type,
        "market": exp.market,
        "strategy": exp.strategy,
        "version": exp.version,
        "status": exp.status,
        "config_path": exp.config_path,
        "created_at": exp.created_at,
        "current_run": exp.current_run,
        "name": exp.name,
        "runs": [
            {
                "run_id": r.run_id,
                "status": r.status,
                "started_at": r.started_at,
                "ended_at": r.ended_at,
                "base_run": r.base_run,
            }
            for r in exp.runs
        ],
    }
    print(json.dumps(result, indent=2, ensure_ascii=False))


def cmd_runs(mgr: ExperimentManager, args: argparse.Namespace) -> None:
    """Handle the 'runs' subcommand."""
    exp_id = args.id
    try:
        run_records = mgr.runs(exp_id)
    except KeyError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    if not run_records:
        print("(no runs)")
        return

    header = f"{'run_id':<28} {'status':<12} {'started_at':<26} {'ended_at':<26} {'base_run':<28}"
    print(header)
    print("-" * len(header))
    for r in run_records:
        print(
            f"{r.run_id:<28} {r.status:<12} {r.started_at:<26} "
            f"{r.ended_at or '-':<26} {r.base_run or '-':<28}"
        )


# ── Argument parser ──────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="exp-cli",
        description="Experiment lifecycle CLI",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # register
    p_reg = sub.add_parser("register", help="Register a new experiment")
    p_reg.add_argument(
        "id",
        help="Experiment spec: type/market/strategy/version (e.g. live/us/ml/2) or canonical id",
    )
    p_reg.add_argument("--config", "-c", help="Path to experiment YAML config", default=None)
    p_reg.add_argument("--name", "-n", help="Human-readable name", default="")
    p_reg.set_defaults(func=cmd_register)

    # start
    p_start = sub.add_parser("start", help="Start an experiment")
    p_start.add_argument("id", help="Experiment id (e.g. live_us_ml_v2)")
    p_start.add_argument("--resume-run", type=str, default="",
                         help="Resume an existing run_id instead of creating a new one")
    p_start.set_defaults(func=cmd_start)

    # pause
    p_pause = sub.add_parser("pause", help="Pause a running experiment")
    p_pause.add_argument("id", help="Experiment id")
    p_pause.set_defaults(func=cmd_pause)

    # resume
    p_resume = sub.add_parser("resume", help="Resume a paused experiment")
    p_resume.add_argument("id", help="Experiment id")
    p_resume.set_defaults(func=cmd_resume)

    # stop
    p_stop = sub.add_parser("stop", help="Stop an experiment (mark completed)")
    p_stop.add_argument("id", help="Experiment id")
    p_stop.set_defaults(func=cmd_stop)

    # restart
    p_restart = sub.add_parser("restart", help="Restart an experiment (stop + start)")
    p_restart.add_argument("id", help="Experiment id")
    p_restart.set_defaults(func=cmd_restart)

    # archive
    p_archive = sub.add_parser("archive", help="Archive a completed or failed experiment")
    p_archive.add_argument("id", help="Experiment id")
    p_archive.set_defaults(func=cmd_archive)

    # list
    p_list = sub.add_parser("list", help="List experiments")
    p_list.add_argument("--type", "-t", choices=["live", "paper", "prod"], help="Filter by type")
    p_list.add_argument(
        "--status", "-s",
        choices=["pending", "running", "paused", "completed", "archived", "failed"],
        help="Filter by status",
    )
    p_list.set_defaults(func=cmd_list)

    # show
    p_show = sub.add_parser("show", help="Show experiment details (JSON)")
    p_show.add_argument("id", help="Experiment id")
    p_show.set_defaults(func=cmd_show)

    # runs
    p_runs = sub.add_parser("runs", help="List run history for an experiment")
    p_runs.add_argument("id", help="Experiment id")
    p_runs.set_defaults(func=cmd_runs)

    return parser


def main(argv: Sequence[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)

    mgr = ExperimentManager()
    args.func(mgr, args)


if __name__ == "__main__":
    main()
