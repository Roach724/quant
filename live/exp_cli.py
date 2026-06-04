#!/usr/bin/env python3
"""Experiment CLI — register, manage, and inspect experiments.

Usage:
    python live/exp_cli.py register live/us/ml/2 --config path/to/config.yaml
    python live/exp_cli.py start    live_us_ml_v2
    python live/exp_cli.py pause    live_us_ml_v2
    python live/exp_cli.py resume   live_us_ml_v2
    python live/exp_cli.py stop     live_us_ml_v2
    python live/exp_cli.py archive  live_us_ml_v2
    python live/exp_cli.py list  [--type live] [--status running]
    python live/exp_cli.py show     live_us_ml_v2
    python live/exp_cli.py runs     live_us_ml_v2
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
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


# ── Command handlers ─────────────────────────────────────────────────

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


def _runner_cmd(config_path: str) -> list[str]:
    """Build the shell command to launch an experiment runner."""
    return [
        "nohup", ".venv/bin/python3", "live/run.py",
        "--config", config_path,
    ]


def _is_pid_alive(pid: int) -> bool:
    """Check if a process with the given PID is still running."""
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False


def cmd_start(mgr: ExperimentManager, args: argparse.Namespace) -> None:
    """Start an experiment: launch daemon process + record PID."""
    exp_id = args.id
    try:
        exp = mgr.get(exp_id)
    except KeyError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    # Check if already running
    existing_pid = mgr.get_pid(exp_id)
    if existing_pid and _is_pid_alive(existing_pid):
        print(f"Error: {exp_id} is already running (PID={existing_pid})", file=sys.stderr)
        sys.exit(1)

    config_path = exp.config_path
    if not config_path:
        print(f"Error: no config_path for {exp_id}", file=sys.stderr)
        sys.exit(1)

    # Set status to paused so Runner can call mgr.start() on its own
    if exp.status != "paused":
        mgr._data[exp_id]["status"] = "paused"
        mgr._save()

    # Launch daemon
    project_root = os.environ.get("QUANT_ROOT", "/opt/quant-prod")
    cmd = _runner_cmd(config_path)
    try:
        proc = subprocess.Popen(
            cmd, cwd=project_root,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True,  # daemonize
        )
    except OSError as e:
        print(f"Error: failed to start process: {e}", file=sys.stderr)
        sys.exit(1)

    mgr.set_pid(exp_id, proc.pid)
    print(f"Started {exp_id} (PID={proc.pid})")


def cmd_stop(mgr: ExperimentManager, args: argparse.Namespace) -> None:
    """Stop an experiment: kill process + clear PID."""
    exp_id = args.id
    try:
        mgr.get(exp_id)
    except KeyError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    pid = mgr.get_pid(exp_id)
    if pid and _is_pid_alive(pid):
        try:
            os.kill(pid, signal.SIGTERM)
            time.sleep(1)
            if _is_pid_alive(pid):
                os.kill(pid, signal.SIGKILL)
                time.sleep(0.5)
            print(f"Stopped {exp_id} (PID={pid})")
        except OSError as e:
            print(f"Error killing PID {pid}: {e}", file=sys.stderr)
    elif pid:
        print(f"Stopped {exp_id} (PID={pid} already dead)")
    else:
        print(f"Stopped {exp_id} (no PID recorded)")

    mgr.set_pid(exp_id, None)
    # Runner's cleanup should have set status; if not, mark paused
    try:
        exp = mgr.get(exp_id)
        if exp.status == "running":
            mgr._data[exp_id]["status"] = "paused"
            mgr._save()
    except KeyError:
        pass


def cmd_restart(mgr: ExperimentManager, args: argparse.Namespace) -> None:
    """Restart an experiment: stop then start."""
    exp_id = args.id
    # Stop
    pid = mgr.get_pid(exp_id)
    if pid and _is_pid_alive(pid):
        try:
            os.kill(pid, signal.SIGTERM)
            time.sleep(1)
            if _is_pid_alive(pid):
                os.kill(pid, signal.SIGKILL)
                time.sleep(0.5)
        except OSError:
            pass
    mgr.set_pid(exp_id, None)

    # Start
    exp = mgr.get(exp_id)
    config_path = exp.config_path
    mgr._data[exp_id]["status"] = "paused"
    mgr._save()

    project_root = os.environ.get("QUANT_ROOT", "/opt/quant-prod")
    proc = subprocess.Popen(
        _runner_cmd(config_path), cwd=project_root,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    mgr.set_pid(exp_id, proc.pid)
    print(f"Restarted {exp_id} (PID={proc.pid})")


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
