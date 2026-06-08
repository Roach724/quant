"""ExperimentManager — lifecycle, run tracking, and BQ logging for experiments.

Core responsibilities:
- Register experiments with canonical IDs: {type}_{market}_{strategy}_v{version}
- Manage state machine: pending → running → paused → completed → archived
- Track runs with globally unique run_id: YYYYMMDD_HHMMSS
- Log run transitions to BigQuery (best-effort, never fails the operation)

Usage:
    from live.experiment_manager import ExperimentManager

    mgr = ExperimentManager()
    mgr.register("live", "us", "ml", 2, "live/configs/exp1_ml_us.yaml", name="ML US v2")
    run_id = mgr.start("live_us_ml_v2")
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────────
REGISTRY_PATH = "/var/quant/experiments/registry.json"
VALID_TYPES = frozenset({"live", "paper", "prod", "debug"})
VALID_MARKETS = frozenset({"us", "hk", "crypto"})
BQ_PROJECT = "deductive-notch-495015-c2"
BQ_DATASET = "quant"
BQ_TABLE = "experiment_runs"


# ── Dataclasses ──────────────────────────────────────────────────────

@dataclass
class RunRecord:
    """Captures a single execution run of an experiment."""
    run_id: str
    status: str                    # running | stopped | completed | failed
    started_at: str                # ISO-8601 UTC
    ended_at: str | None = None    # ISO-8601 UTC
    base_run: str | None = None    # resume 时的上一轮 run_id


@dataclass
class Experiment:
    """Full experiment descriptor backed by the JSON registry.

    Status is now **derived** from runs, not stored:
      - If any run has status='running' → status='running'
      - Otherwise → status='idle'
    """
    id: str
    type: str
    market: str
    strategy: str
    version: int
    status: str                    # derived: 'running' | 'idle'
    config_path: str
    created_at: str                # ISO-8601 UTC
    current_run: str | None = None
    name: str = ""
    runs: list[RunRecord] = field(default_factory=list)

    # ── Derived properties ────────────────────────────────────────

    @property
    def has_active_run(self) -> bool:
        """True if any run is currently 'running'."""
        return any(r.status == "running" for r in self.runs)

    @property
    def total_runs(self) -> int:
        """Total number of runs (including completed/failed/stopped)."""
        return len(self.runs)

    @property
    def active_run(self) -> RunRecord | None:
        """The currently active run, or None."""
        for r in self.runs:
            if r.status == "running":
                return r
        return None

    # ── State guards (simplified) ──────────────────────────────────

    @property
    def can_start(self) -> bool:
        """Can start if no run is currently running."""
        return not self.has_active_run

    @property
    def can_stop(self) -> bool:
        """Can stop if there is an active run."""
        return self.has_active_run

    @property
    def can_delete(self) -> bool:
        """Can delete only if no active run."""
        return not self.has_active_run


# ── ID helpers ───────────────────────────────────────────────────────

def _build_id(exp_type: str, market: str, strategy: str, version: int) -> str:
    """Canonical experiment id: {type}_{market}_{strategy}_v{version}."""
    return f"{exp_type}_{market}_{strategy}_v{version}"


_last_run_id_ts: str = ""


def _make_run_id() -> str:
    """Globally unique run id: YYYYMMDD_HHMMSS_ffffff.

    Uses microsecond precision to prevent collisions within the same second.
    """
    import time as _t
    global _last_run_id_ts
    while True:
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
        if ts != _last_run_id_ts:
            _last_run_id_ts = ts
            return ts
        _t.sleep(0.000001)


# ── PID helper ──────────────────────────────────────────────────────

def _is_pid_alive(pid: int) -> bool:
    """Check if a process with the given PID is still running."""
    try:
        import signal
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False


def _is_unit_active(exp_id: str) -> bool:
    """Check if experiment process is alive (Docker: PID file)."""
    pid_file = f"/var/quant/state/{exp_id}.pid"
    if not os.path.exists(pid_file):
        return False
    try:
        with open(pid_file) as pf:
            pid = int(pf.read().strip())
        os.kill(pid, 0)
        return True
    except (ValueError, OSError, ProcessLookupError):
        return False


# ── ExperimentManager ────────────────────────────────────────────────

class ExperimentManager:
    """JSON-file-backed registry for experiment lifecycle management.

    Registry is stored at /var/quant/experiments/registry.json.
    Auto-creates the file and parent directories on first use.
    Best-effort BQ logging on start/resume; never blocks or raises.
    """

    def __init__(self, registry_path: str | None = None):
        self._path = Path(registry_path or REGISTRY_PATH)
        self._data: dict[str, dict[str, Any]] = {}
        self._load()

    # ── Internal helpers ─────────────────────────────────────────────

    def _load(self) -> None:
        """Load registry from disk, creating an empty one if necessary."""
        if self._path.exists():
            try:
                raw = json.loads(self._path.read_text())
                self._data = raw.get("experiments", {})
                logger.debug("Loaded %d experiments from %s", len(self._data), self._path)
            except (json.JSONDecodeError, ValueError):
                logger.warning("Corrupt registry at %s — starting fresh", self._path)
                self._data = {}
                self._save()
        else:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._data = {}
            self._save()
            logger.info("Initialized empty registry at %s", self._path)

    def _save(self) -> None:
        """Atomically write the in-memory registry to disk."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"experiments": self._data}
        # Write to temp then rename for atomicity
        tmp_path = self._path.with_suffix(self._path.suffix + ".tmp")
        tmp_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
        os.replace(tmp_path, self._path)

    def get_pid(self, exp_id: str) -> int | None:
        d = self._data.get(exp_id)
        if d is None:
            raise KeyError(f"Experiment '{exp_id}' not found")
        return d.get("pid")

    def set_pid(self, exp_id: str, pid: int | None) -> None:
        d = self._data.get(exp_id)
        if d is None:
            raise KeyError(f"Experiment '{exp_id}' not found")
        d["pid"] = pid
        self._save()

    def delete(self, exp_id: str) -> None:
        """Remove experiment from registry entirely."""
        if exp_id not in self._data:
            raise KeyError(f"Experiment '{exp_id}' not found")
        del self._data[exp_id]
        self._save()

    def _get_exp(self, exp_id: str) -> dict[str, Any]:
        """Look up an experiment dict, raising KeyError if missing."""
        if exp_id not in self._data:
            raise KeyError(f"Experiment '{exp_id}' not found in registry")
        return self._data[exp_id]

    def _log_run_to_bq(
        self,
        exp_id: str,
        run_id: str,
        status: str,
        base_run: str | None = None,
    ) -> None:
        """Best-effort: write a row to quant.experiment_runs in BigQuery.

        Catches ALL exceptions — never raises. Logs a warning on failure.
        In test/dev environments without BQ credentials this silently succeeds
        by not crashing.
        """
        try:
            from google.cloud import bigquery

            client = bigquery.Client(project=BQ_PROJECT)
            table_ref = f"{BQ_PROJECT}.{BQ_DATASET}.{BQ_TABLE}"
            now_iso = datetime.now(timezone.utc).isoformat()

            row = {
                "run_id": run_id,
                "exp_id": exp_id,
                "status": status,
                "started_at": now_iso,
                "ended_at": None if status in ("running", "paused") else now_iso,
                "base_run": base_run,
                "notes": "",
            }
            errors = client.insert_rows_json(table_ref, [row])
            if errors:
                logger.warning("BQ insert returned errors for run %s: %s", run_id, errors)
            else:
                logger.info("BQ logged run %s → %s (status=%s)", run_id, exp_id, status)
        except Exception:
            logger.warning(
                "BQ logging failed for run %s (exp=%s status=%s) — non-fatal",
                run_id, exp_id, status,
                exc_info=True,
            )

    # ── Public API ───────────────────────────────────────────────────

    def register(
        self,
        exp_type: str,
        market: str,
        strategy: str,
        version: int,
        config_path: str,
        name: str = "",
        exp_id: str = "",
    ) -> str:
        """Register a new experiment. If exp_id is given, use it; otherwise build from type/market/strategy/version."""
        if exp_type not in VALID_TYPES:
            raise ValueError(
                f"Invalid type '{exp_type}' — must be one of {sorted(VALID_TYPES)}"
            )
        if market not in VALID_MARKETS:
            raise ValueError(
                f"Invalid market '{market}' — must be one of {sorted(VALID_MARKETS)}"
            )
        if not isinstance(version, int) or version < 1:
            raise ValueError(f"version must be a positive int, got {version!r}")

        exp_id = exp_id or _build_id(exp_type, market, strategy, version)

        if exp_id in self._data:
            raise ValueError(f"Experiment '{exp_id}' is already registered")

        now_iso = datetime.now(timezone.utc).isoformat()
        entry: dict[str, Any] = {
            "id": exp_id,
            "type": exp_type,
            "market": market,
            "strategy": strategy,
            "version": version,
            "status": "pending",
            "config_path": config_path,
            "created_at": now_iso,
            "current_run": None,
            "name": name,
            "runs": [],
        }
        self._data[exp_id] = entry
        self._save()
        logger.info("Registered experiment: %s (type=%s market=%s)", exp_id, exp_type, market)
        return exp_id

    def get(self, exp_id: str) -> Experiment:
        """Retrieve a single experiment by id.

        Raises KeyError if not found.
        """
        entry = self._get_exp(exp_id)
        return _to_experiment(entry)

    def list(
        self,
        exp_type: str | None = None,
        status: str | None = None,
    ) -> list[Experiment]:
        """List experiments, optionally filtered by type and/or status.

        Parameters
        ----------
        exp_type : filter by type ('live', 'paper', 'prod'), or None for all
        status : filter by status, or None for all
        """
        results = []
        for eid, entry in self._data.items():
            if exp_type is not None and entry.get("type") != exp_type:
                continue
            if status is not None and entry.get("status") != status:
                continue
            results.append(_to_experiment(entry))
        return results

    def runs(self, exp_id: str) -> list[RunRecord]:
        """Return the run history for an experiment, newest first."""
        entry = self._get_exp(exp_id)
        return _to_experiment(entry).runs

    # ── Lifecycle operations ─────────────────────────────────────────

    def active_run(self, exp_id: str) -> RunRecord | None:
        """Return the currently active run for an experiment, or None."""
        entry = self._get_exp(exp_id)
        exp = _to_experiment(entry)
        return exp.active_run

    def start(self, exp_id: str) -> str:
        """Start a new run for an experiment.

        Refuses if there is already an active run.
        Returns the new run_id.
        """
        entry = self._get_exp(exp_id)
        exp = _to_experiment(entry)

        if not exp.can_start:
            active = exp.active_run
            raise RuntimeError(
                f"Cannot start experiment '{exp_id}': "
                f"already has active run {active.run_id if active else '?'}"
            )

        run_id = _make_run_id()
        now_iso = datetime.now(timezone.utc).isoformat()

        run_record: dict[str, Any] = {
            "run_id": run_id,
            "status": "running",
            "started_at": now_iso,
            "ended_at": None,
            "base_run": None,
        }
        entry.setdefault("runs", []).append(run_record)
        entry["current_run"] = run_id
        self._save()

        logger.info("Started experiment %s → run_id=%s", exp_id, run_id)
        self._log_run_to_bq(exp_id, run_id, "running")
        return run_id

    def stop_run(self, exp_id: str, run_id: str) -> None:
        """Stop a specific run — mark it as 'stopped' (manual intervention).

        Only works on runs with status='running'.
        """
        entry = self._get_exp(exp_id)
        now_iso = datetime.now(timezone.utc).isoformat()

        runs = entry.setdefault("runs", [])
        found = False
        for r in runs:
            if r["run_id"] == run_id and r.get("status") == "running":
                r["status"] = "stopped"
                r["ended_at"] = now_iso
                found = True
                break

        if not found:
            raise RuntimeError(
                f"Run '{run_id}' not found or not in 'running' state for experiment '{exp_id}'"
            )

        # Clear current_run if it matches
        if entry.get("current_run") == run_id:
            entry["current_run"] = None
        self._save()

        logger.info("Stopped run %s of experiment %s", run_id, exp_id)
        self._log_run_to_bq(exp_id, run_id, "stopped")

    def auto_heal(self, exp_id: str) -> list[str]:
        """Check experiment PID/systemd unit and auto-complete stale runs.

        If the experiment has a running run but the PID is dead AND the
        systemd unit is inactive, mark the run as 'completed'.

        Returns list of run_ids that were auto-completed.
        """
        entry = self._get_exp(exp_id)
        healed: list[str] = []

        pid = entry.get("pid")
        pid_dead = pid is not None and not _is_pid_alive(pid)
        unit_dead = not _is_unit_active(exp_id)

        # Only heal if both PID and systemd unit are dead
        if not pid_dead and not unit_dead:
            return healed
        if pid is not None and not pid_dead:
            return healed  # PID alive, don't heal
        if not unit_dead and pid is None:
            return healed  # Unit active, just missing PID - don't heal

        # Both dead (or PID dead + no unit) — mark running runs as completed
        now_iso = datetime.now(timezone.utc).isoformat()
        runs = entry.setdefault("runs", [])
        for r in runs:
            if r.get("status") == "running" and not r.get("ended_at"):
                r["status"] = "completed"
                r["ended_at"] = now_iso
                healed.append(r["run_id"])
                logger.info("Auto-healed stale run %s → completed (PID %s dead, unit inactive)",
                            r["run_id"], pid)

        if healed:
            entry["current_run"] = None
            entry["pid"] = None
            self._save()

        return healed

    def startup_heal(self, exp_id: str) -> list[str]:
        """On server startup, mark dead-PID runs as 'failed' (unexpected death).

        Unlike auto_heal() which marks as 'completed', this marks as 'failed'
        because the process was killed externally (reboot, OOM, etc.).
        The user must manually review before re-running.

        Returns list of run_ids that were marked failed.
        """
        entry = self._get_exp(exp_id)
        healed: list[str] = []

        pid = entry.get("pid")
        pid_dead = pid is not None and not _is_pid_alive(pid)
        unit_dead = not _is_unit_active(exp_id)

        if not pid_dead and not unit_dead:
            return healed
        if pid is not None and not pid_dead:
            return healed
        if not unit_dead and pid is None:
            return healed

        now_iso = datetime.now(timezone.utc).isoformat()
        runs = entry.setdefault("runs", [])
        for r in runs:
            if r.get("status") == "running" and not r.get("ended_at"):
                r["status"] = "failed"
                r["ended_at"] = now_iso
                healed.append(r["run_id"])
                logger.warning("Startup heal: stale run %s \u2192 failed (process killed externally)",
                               r["run_id"])

        if healed:
            entry["current_run"] = None
            entry["pid"] = None
            self._save()

        return healed

    def pause(self, exp_id: str) -> None:
        """Pause a running experiment."""
        entry = self._get_exp(exp_id)
        exp = _to_experiment(entry)

        if not exp.can_pause:
            raise RuntimeError(
                f"Cannot pause experiment '{exp_id}': current status={exp.status}"
            )

        now_iso = datetime.now(timezone.utc).isoformat()
        current_run = entry.get("current_run")

        # Mark the current run as paused
        runs = entry.setdefault("runs", [])
        for r in runs:
            if r["run_id"] == current_run and r["status"] in ("running",):
                r["status"] = "paused"
                r["ended_at"] = now_iso
                break

        entry["status"] = "paused"
        self._save()

        logger.info("Paused experiment %s (run=%s)", exp_id, current_run)
        if current_run:
            self._log_run_to_bq(exp_id, current_run, "paused")

    def resume(self, exp_id: str) -> str:
        """Resume a paused experiment — creates a new run chained to the previous one.

        Returns the new run_id.
        """
        entry = self._get_exp(exp_id)
        exp = _to_experiment(entry)

        if not exp.can_resume:
            raise RuntimeError(
                f"Cannot resume experiment '{exp_id}': current status={exp.status}"
            )

        previous_run = entry.get("current_run")
        new_run_id = _make_run_id()
        now_iso = datetime.now(timezone.utc).isoformat()

        run_record: dict[str, Any] = {
            "run_id": new_run_id,
            "status": "running",
            "started_at": now_iso,
            "ended_at": None,
            "base_run": previous_run,
        }
        entry.setdefault("runs", []).append(run_record)
        entry["status"] = "running"
        entry["current_run"] = new_run_id
        self._save()

        logger.info("Resumed experiment %s → run_id=%s (base=%s)", exp_id, new_run_id, previous_run)
        self._log_run_to_bq(exp_id, new_run_id, "running", base_run=previous_run)
        return new_run_id

    def stop(self, exp_id: str) -> None:
        """Stop an experiment — marks it as completed."""
        entry = self._get_exp(exp_id)
        exp = _to_experiment(entry)

        if not exp.can_stop:
            raise RuntimeError(
                f"Cannot stop experiment '{exp_id}': current status={exp.status}"
            )

        now_iso = datetime.now(timezone.utc).isoformat()
        current_run = entry.get("current_run")

        runs = entry.setdefault("runs", [])
        for r in runs:
            if r["run_id"] == current_run and r.get("status") in ("running", "paused"):
                r["status"] = "completed"
                r["ended_at"] = now_iso
                break

        entry["status"] = "completed"
        self._save()

        logger.info("Stopped experiment %s (run=%s)", exp_id, current_run)
        if current_run:
            self._log_run_to_bq(exp_id, current_run, "completed")

    def archive(self, exp_id: str) -> None:
        """Archive a completed or failed experiment."""
        entry = self._get_exp(exp_id)
        exp = _to_experiment(entry)

        if not exp.can_archive:
            raise RuntimeError(
                f"Cannot archive experiment '{exp_id}': current status={exp.status}"
            )

        entry["status"] = "archived"
        self._save()
        logger.info("Archived experiment %s", exp_id)

    def fail(self, exp_id: str, notes: str = "") -> None:
        """Mark an experiment as failed (error state).

        Parameters
        ----------
        exp_id : experiment id
        notes : optional description of the failure reason
        """
        entry = self._get_exp(exp_id)
        now_iso = datetime.now(timezone.utc).isoformat()
        current_run = entry.get("current_run")

        runs = entry.setdefault("runs", [])
        for r in runs:
            if r["run_id"] == current_run and r.get("status") in ("running", "paused"):
                r["status"] = "failed"
                r["ended_at"] = now_iso
                break

        entry["status"] = "failed"
        self._save()

        logger.warning("Marked experiment %s as failed: %s", exp_id, notes or "(no notes)")
        if current_run:
            self._log_run_to_bq(exp_id, current_run, "failed")

    def delete_run(self, exp_id: str, run_id: str) -> dict:
        """Permanently delete a run and all associated data.

        Cascading deletes:
        - Registry entry removal
        - BQ tables: experiment_equity + experiment_trades (DELETE WHERE run_id)
        - State directory: /var/quant/state/{exp_id}/{run_id}
        - Output directory: /opt/quant/output/live/*{exp_id}_{run_id}
        - Log files: /var/log/quant/prod/{module}/{exp_id}_{run_id}.log

        Refuses if the run is currently 'running'.
        Returns a dict summarising what was deleted.
        """
        import glob as _glob
        import shutil

        entry = self._get_exp(exp_id)
        result: dict[str, str] = {}

        # Guard: refuse if any run is running
        runs = entry.setdefault("runs", [])
        for r in runs:
            if r["run_id"] == run_id and r.get("status") == "running":
                raise RuntimeError(
                    f"Cannot delete run '{run_id}': it is still running for experiment '{exp_id}'"
                )

        # 1. Remove from registry
        entry["runs"] = [r for r in runs if r.get("run_id") != run_id]
        if entry.get("current_run") == run_id:
            entry["current_run"] = None
        self._save()
        result["registry"] = "removed"

        # 2. Delete BQ data for this run
        try:
            from google.cloud import bigquery
            client = bigquery.Client(project=BQ_PROJECT)
            for table in ["experiment_equity", "experiment_trades", "experiment_runs"]:
                q = (
                    f"DELETE FROM `{BQ_PROJECT}.{BQ_DATASET}.{table}` "
                    f"WHERE run_id = @run_id"
                )
                job_config = bigquery.QueryJobConfig(
                    query_parameters=[
                        bigquery.ScalarQueryParameter("run_id", "STRING", run_id)
                    ]
                )
                client.query(q, job_config=job_config).result()
                result[f"bq_{table}"] = "deleted"
        except Exception as e:
            result["bq_error"] = str(e)[:120]

        # 3. Delete state directory
        state_dir = Path(f"/var/quant/state/{exp_id}/{run_id}")
        if state_dir.exists():
            shutil.rmtree(state_dir)
            result["state"] = "deleted"
        else:
            result["state"] = "not found"

        # 4. Delete output directory (contains equity_curve.csv, trades.csv, etc.)
        output_pattern = f"/opt/quant/output/live/*_{exp_id}_{run_id}"
        for d in _glob.glob(output_pattern):
            if Path(d).is_dir():
                shutil.rmtree(d)
                result["output"] = "deleted"
        if "output" not in result:
            result["output"] = "not found"

        # 5. Delete log files
        for module in ["live", "paper_run"]:
            log_file = Path(f"/var/log/quant/prod/{module}/{exp_id}_{run_id}.log")
            if log_file.exists():
                log_file.unlink()
                result[f"log_{module}"] = "deleted"

        logger.info("Deleted run %s of %s: %s", run_id, exp_id, result)
        return result

    def clear_run_state(self, exp_id: str, run_id: str) -> dict:
        """Clear state and checkpoint for a run (keeps BQ data and logs).

        Useful for resetting a run to re-run from scratch while preserving
        historical equity/trade records.

        Only deletes /var/quant/state/{exp_id}/{run_id}.
        Refuses if the run is currently 'running'.
        Returns a dict summarising what was deleted.
        """
        import shutil

        entry = self._get_exp(exp_id)
        result: dict[str, str] = {}

        # Guard: refuse if this run is running
        runs = entry.setdefault("runs", [])
        for r in runs:
            if r["run_id"] == run_id and r.get("status") == "running":
                raise RuntimeError(
                    f"Cannot clear state for run '{run_id}': "
                    f"it is still running for experiment '{exp_id}'"
                )

        # Only delete the state/checkpoint directory
        state_dir = Path(f"/var/quant/state/{exp_id}/{run_id}")
        if state_dir.exists():
            shutil.rmtree(state_dir)
            result["state"] = "deleted"
        else:
            result["state"] = "not found"

        logger.info("Cleared state for run %s of %s", run_id, exp_id)
        return result


# ── Deserialization helper ───────────────────────────────────────────

def _to_experiment(entry: dict[str, Any]) -> Experiment:
    """Convert a raw registry dict entry into an Experiment dataclass.

    Status is derived from runs: 'running' if any run is active, else 'idle'.
    """
    raw_runs = entry.get("runs", [])
    runs = [
        RunRecord(
            run_id=r["run_id"],
            status=r.get("status", "unknown"),
            started_at=r.get("started_at", ""),
            ended_at=r.get("ended_at"),
            base_run=r.get("base_run"),
        )
        for r in raw_runs
    ]
    # Derive status from runs
    if any(r.status == "running" for r in runs):
        derived_status = "running"
    else:
        derived_status = "idle"
    return Experiment(
        id=entry["id"],
        type=entry["type"],
        market=entry["market"],
        strategy=entry["strategy"],
        version=entry["version"],
        status=derived_status,
        config_path=entry["config_path"],
        created_at=entry["created_at"],
        current_run=entry.get("current_run"),
        name=entry.get("name", ""),
        runs=runs,
    )
