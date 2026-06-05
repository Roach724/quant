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
    status: str                    # running | paused | completed | failed
    started_at: str                # ISO-8601 UTC
    ended_at: str | None = None    # ISO-8601 UTC
    base_run: str | None = None    # resume 时的上一轮 run_id


@dataclass
class Experiment:
    """Full experiment descriptor backed by the JSON registry."""
    id: str
    type: str
    market: str
    strategy: str
    version: int
    status: str                    # pending | running | paused | completed | archived | failed
    config_path: str
    created_at: str                # ISO-8601 UTC
    current_run: str | None = None
    name: str = ""
    runs: list[RunRecord] = field(default_factory=list)

    # ── State guards ──────────────────────────────────────────────

    @property
    def can_start(self) -> bool:
        """Ready to start: pending, paused (resume), completed (replay), archived (revive),
        or running with no current run (migrated experiment)."""
        if self.status == "running" and not self.current_run:
            return True
        return self.status in ("pending", "paused", "completed", "archived")

    @property
    def can_pause(self) -> bool:
        """Only a running experiment can be paused."""
        return self.status == "running"

    @property
    def can_resume(self) -> bool:
        """Only a paused experiment can be resumed."""
        return self.status == "paused"

    @property
    def can_stop(self) -> bool:
        """Running or paused experiments can be stopped (marked completed)."""
        return self.status in ("running", "paused")

    @property
    def can_archive(self) -> bool:
        """Only completed or failed experiments can be archived."""
        return self.status in ("completed", "failed")


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
    ) -> str:
        """Register a new experiment and return its canonical id.

        Parameters
        ----------
        exp_type : {'live', 'paper', 'prod'}
        market : {'us', 'hk', 'crypto'}
        strategy : short strategy name, e.g. 'ml', 'mom'
        version : positive integer
        config_path : relative path to the YAML config
        name : optional human-readable label

        Returns
        -------
        str : canonical experiment id, e.g. 'live_us_ml_v2'

        Raises
        ------
        ValueError : invalid type/market/version or duplicate id
        """
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

        exp_id = _build_id(exp_type, market, strategy, version)

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

    def start(self, exp_id: str) -> str:
        """Start an experiment: validate state, create run, mark running.

        Returns the new run_id.
        Raises RuntimeError if the experiment cannot be started.
        """
        entry = self._get_exp(exp_id)
        exp = _to_experiment(entry)

        if not exp.can_start:
            prev_run = entry.get("current_run") or "none"
            raise RuntimeError(
                f"Cannot start experiment '{exp_id}': "
                f"status={exp.status}, current_run={prev_run}"
            )

        run_id = _make_run_id()
        now_iso = datetime.now(timezone.utc).isoformat()

        # Close any previous run that was left in a non-terminal state
        runs = entry.setdefault("runs", [])
        for r in runs:
            if r.get("status") in ("running",) and not r.get("ended_at"):
                r["status"] = "paused"
                r["ended_at"] = now_iso

        run_record: dict[str, Any] = {
            "run_id": run_id,
            "status": "running",
            "started_at": now_iso,
            "ended_at": None,
            "base_run": None,
        }
        runs.append(run_record)
        entry["status"] = "running"
        entry["current_run"] = run_id
        self._save()

        logger.info("Started experiment %s → run_id=%s", exp_id, run_id)
        self._log_run_to_bq(exp_id, run_id, "running")
        return run_id

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


# ── Deserialization helper ───────────────────────────────────────────

def _to_experiment(entry: dict[str, Any]) -> Experiment:
    """Convert a raw registry dict entry into an Experiment dataclass."""
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
    return Experiment(
        id=entry["id"],
        type=entry["type"],
        market=entry["market"],
        strategy=entry["strategy"],
        version=entry["version"],
        status=entry["status"],
        config_path=entry["config_path"],
        created_at=entry["created_at"],
        current_run=entry.get("current_run"),
        name=entry.get("name", ""),
        runs=runs,
    )
