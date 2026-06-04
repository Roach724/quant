# Experiment Management System — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace ad-hoc experiment management with a unified ExperimentManager: standardized IDs, lifecycle states, run-level data isolation, and Dashboard type-separated tabs.

**Architecture:** ExperimentManager class handles registration + lifecycle + run generation. CLI wraps it. BQ schema adds run_id to equity/trades tables + new experiment_runs table. Dashboard filters by experiment type prefix and run_id.

**Tech Stack:** Python 3.12, BigQuery, FastAPI, Vue 3, JSON file registry

**Spec:** `docs/superpowers/specs/2026-06-04-experiment-management-design.md`

---

## Phase 0: BQ Schema Changes

### Task 0.1: Create experiment_runs table

**Files:**
- BQ: `quant.experiment_runs` (new)

- [ ] **Step 1: Create table via BQ console or CLI**

```sql
CREATE TABLE IF NOT EXISTS `deductive-notch-495015-c2.quant.experiment_runs` (
  run_id STRING NOT NULL,
  exp_id STRING NOT NULL,
  status STRING,
  started_at TIMESTAMP,
  ended_at TIMESTAMP,
  base_run STRING,
  notes STRING
);
```

- [ ] **Step 2: Add run_id column to experiment_equity**

```sql
ALTER TABLE `deductive-notch-495015-c2.quant.experiment_equity`
ADD COLUMN IF NOT EXISTS run_id STRING;
```

- [ ] **Step 3: Add run_id column to experiment_trades**

```sql
ALTER TABLE `deductive-notch-495015-c2.quant.experiment_trades`
ADD COLUMN IF NOT EXISTS run_id STRING;
```

- [ ] **Step 4: Verify columns exist**

```bash
bq query --use_legacy_sql=false \
  "SELECT column_name FROM deductive-notch-495015-c2.quant.INFORMATION_SCHEMA.COLUMNS WHERE table_name='experiment_equity'"
```

Expected: `run_id` appears in output.

---

## Phase 1: Core — ExperimentManager

### Task 1.1: Create ExperimentManager class skeleton

**Files:**
- Create: `live/experiment_manager.py`
- Create: `tests/test_experiment_manager.py`

- [ ] **Step 1: Write failing test for register**

```python
# tests/test_experiment_manager.py
import json
import tempfile
import os
from live.experiment_manager import ExperimentManager

def test_register_creates_entry():
    with tempfile.TemporaryDirectory() as tmpdir:
        registry_path = os.path.join(tmpdir, "registry.json")
        mgr = ExperimentManager(registry_path=registry_path)
        exp = mgr.register(
            _type="live", market="us", strategy="ml", version=2,
            config_path="live/configs/exp1_ml_us.yaml",
        )
        assert exp.id == "live_us_ml_v2"
        assert exp.status == "pending"
        assert exp.type == "live"

        # verify file written
        with open(registry_path) as f:
            data = json.load(f)
        assert "live_us_ml_v2" in data["experiments"]
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /opt/quant-prod && PYTHONPATH=/opt/quant-prod .venv/bin/python3 -m pytest tests/test_experiment_manager.py::test_register_creates_entry -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'live.experiment_manager'`

- [ ] **Step 3: Implement ExperimentManager and Experiment dataclass**

```python
# live/experiment_manager.py
"""ExperimentManager — unified experiment lifecycle management."""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

DEFAULT_REGISTRY = "/var/quant/experiments/registry.json"

VALID_TYPES = frozenset({"live", "paper", "prod"})
VALID_MARKETS = frozenset({"us", "hk", "crypto"})
VALID_STATUSES = frozenset({"pending", "running", "paused", "completed", "archived", "failed"})


@dataclass
class RunRecord:
    run_id: str
    status: str
    started_at: str
    ended_at: Optional[str] = None
    base_run: Optional[str] = None


@dataclass
class Experiment:
    id: str
    type: str          # live | paper | prod
    market: str        # us | hk | crypto
    strategy: str      # ml | mom | ...
    version: int
    status: str        # pending | running | paused | completed | archived | failed
    config_path: str
    created_at: str
    current_run: Optional[str] = None
    name: str = ""
    runs: list[RunRecord] = field(default_factory=list)

    @property
    def is_running(self) -> bool:
        return self.status == "running"

    @property
    def can_start(self) -> bool:
        return self.status in ("pending", "paused", "completed", "archived")

    @property
    def can_pause(self) -> bool:
        return self.status == "running"

    @property
    def can_resume(self) -> bool:
        return self.status == "paused"

    @property
    def can_stop(self) -> bool:
        return self.status in ("running", "paused")

    @property
    def can_archive(self) -> bool:
        return self.status in ("completed", "failed")


def _build_id(_type: str, market: str, strategy: str, version: int) -> str:
    return f"{_type}_{market}_{strategy}_v{version}"


def _make_run_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


class ExperimentManager:
    """Manages experiment registration, lifecycle, and run tracking."""

    def __init__(self, registry_path: str = DEFAULT_REGISTRY):
        self._path = Path(registry_path)
        self._data: dict = {"experiments": {}}
        if self._path.exists():
            try:
                self._data = json.loads(self._path.read_text())
            except Exception:
                logger.warning("Corrupt registry, starting fresh")
                self._data = {"experiments": {}}
        else:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._save()

    def _save(self):
        self._path.write_text(json.dumps(self._data, indent=2, default=str))

    # ── Registration ──

    def register(
        self, _type: str, market: str, strategy: str, version: int,
        config_path: str, name: str = "",
    ) -> Experiment:
        _type = _type.lower()
        market = market.lower()
        strategy = strategy.lower()

        if _type not in VALID_TYPES:
            raise ValueError(f"Invalid type '{_type}'. Must be one of {VALID_TYPES}")
        if market not in VALID_MARKETS:
            raise ValueError(f"Invalid market '{market}'")
        if version < 1:
            raise ValueError("Version must be >= 1")

        exp_id = _build_id(_type, market, strategy, version)
        if exp_id in self._data["experiments"]:
            raise ValueError(f"Experiment '{exp_id}' already registered")

        exp = Experiment(
            id=exp_id,
            type=_type,
            market=market,
            strategy=strategy,
            version=version,
            status="pending",
            config_path=config_path,
            created_at=datetime.now(timezone.utc).isoformat(),
            name=name or f"{_type.upper()} {market.upper()} {strategy} v{version}",
        )
        self._data["experiments"][exp_id] = _exp_to_dict(exp)
        self._save()
        logger.info("Registered: %s", exp_id)
        return exp

    # ── Lookup ──

    def get(self, exp_id: str) -> Experiment:
        d = self._data["experiments"].get(exp_id)
        if d is None:
            raise KeyError(f"Experiment '{exp_id}' not found")
        return _dict_to_exp(d)

    def list(self, _type: Optional[str] = None, status: Optional[str] = None) -> list[Experiment]:
        result = []
        for d in self._data["experiments"].values():
            exp = _dict_to_exp(d)
            if _type and exp.type != _type:
                continue
            if status and exp.status != status:
                continue
            result.append(exp)
        return result

    def runs(self, exp_id: str) -> list[RunRecord]:
        exp = self.get(exp_id)
        return exp.runs

    # ── Lifecycle ──

    def start(self, exp_id: str, force: bool = False) -> str:
        """Start an experiment. Returns run_id."""
        exp = self.get(exp_id)
        if not force and not exp.can_start:
            raise RuntimeError(f"Cannot start '{exp_id}': status is '{exp.status}'")

        run_id = _make_run_id()
        self._update(exp_id, status="running", current_run=run_id)
        self._add_run(exp_id, run_id, "running")
        self._save()
        logger.info("Started %s → run %s", exp_id, run_id)
        return run_id

    def pause(self, exp_id: str):
        exp = self.get(exp_id)
        if not exp.can_pause:
            raise RuntimeError(f"Cannot pause '{exp_id}': status is '{exp.status}'")

        now = datetime.now(timezone.utc).isoformat()
        self._update(exp_id, status="paused")
        self._end_current_run(exp_id, "paused", now)
        self._save()
        logger.info("Paused %s", exp_id)

    def resume(self, exp_id: str) -> str:
        """Resume a paused experiment. Returns new run_id."""
        exp = self.get(exp_id)
        if not exp.can_resume:
            raise RuntimeError(f"Cannot resume '{exp_id}': status is '{exp.status}'")

        prev_run = exp.current_run
        run_id = _make_run_id()
        self._update(exp_id, status="running", current_run=run_id)
        self._add_run(exp_id, run_id, "running", base_run=prev_run)
        self._save()
        logger.info("Resumed %s → run %s (base: %s)", exp_id, run_id, prev_run)
        return run_id

    def stop(self, exp_id: str):
        exp = self.get(exp_id)
        if not exp.can_stop:
            raise RuntimeError(f"Cannot stop '{exp_id}': status is '{exp.status}'")

        now = datetime.now(timezone.utc).isoformat()
        self._update(exp_id, status="completed")
        self._end_current_run(exp_id, "completed", now)
        self._save()
        logger.info("Stopped %s", exp_id)

    def archive(self, exp_id: str):
        exp = self.get(exp_id)
        if not exp.can_archive:
            raise RuntimeError(f"Cannot archive '{exp_id}': status is '{exp.status}'")

        self._update(exp_id, status="archived")
        self._save()
        logger.info("Archived %s", exp_id)

    def fail(self, exp_id: str, notes: str = ""):
        """Mark experiment as failed (called by runner on error)."""
        exp = self.get(exp_id)
        now = datetime.now(timezone.utc).isoformat()
        self._update(exp_id, status="failed")
        self._end_current_run(exp_id, "failed", now)
        self._save()
        logger.error("Failed %s: %s", exp_id, notes)

    # ── Internals ──

    def _update(self, exp_id: str, **kwargs):
        d = self._data["experiments"].get(exp_id)
        if d is None:
            raise KeyError(f"Experiment '{exp_id}' not found")
        d.update(kwargs)

    def _add_run(self, exp_id: str, run_id: str, status: str, base_run: Optional[str] = None):
        d = self._data["experiments"][exp_id]
        d.setdefault("runs", []).append({
            "run_id": run_id,
            "status": status,
            "started_at": datetime.now(timezone.utc).isoformat(),
            "ended_at": None,
            "base_run": base_run,
        })

    def _end_current_run(self, exp_id: str, status: str, ended_at: str):
        d = self._data["experiments"][exp_id]
        for run in reversed(d.get("runs", [])):
            if run["run_id"] == d.get("current_run"):
                run["status"] = status
                run["ended_at"] = ended_at
                break


def _exp_to_dict(exp: Experiment) -> dict:
    d = asdict(exp)
    return d


def _dict_to_exp(d: dict) -> Experiment:
    runs = [RunRecord(**r) for r in d.get("runs", [])]
    d = {**d}
    d["runs"] = runs
    return Experiment(**{k: v for k, v in d.items() if k in Experiment.__dataclass_fields__})
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd /opt/quant-prod && PYTHONPATH=/opt/quant-prod .venv/bin/python3 -m pytest tests/test_experiment_manager.py::test_register_creates_entry -v
```

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add live/experiment_manager.py tests/test_experiment_manager.py
git commit -m "feat: ExperimentManager — register + lifecycle + run tracking"
```

### Task 1.2: Add lifecycle tests

**Files:**
- Modify: `tests/test_experiment_manager.py`

- [ ] **Step 1: Write lifecycle tests**

```python
# append to tests/test_experiment_manager.py

def test_register_duplicate_rejected():
    import tempfile, os
    from live.experiment_manager import ExperimentManager
    with tempfile.TemporaryDirectory() as tmpdir:
        mgr = ExperimentManager(registry_path=os.path.join(tmpdir, "r.json"))
        mgr.register("live", "us", "ml", 1, "cfg.yaml")
        import pytest
        with pytest.raises(ValueError, match="already registered"):
            mgr.register("live", "us", "ml", 1, "cfg.yaml")


def test_full_lifecycle():
    import tempfile, os
    from live.experiment_manager import ExperimentManager
    with tempfile.TemporaryDirectory() as tmpdir:
        mgr = ExperimentManager(registry_path=os.path.join(tmpdir, "r.json"))
        mgr.register("live", "us", "ml", 1, "cfg.yaml")

        # start
        run1 = mgr.start("live_us_ml_v1")
        assert run1 is not None
        exp = mgr.get("live_us_ml_v1")
        assert exp.status == "running"
        assert exp.current_run == run1
        assert len(exp.runs) == 1
        assert exp.runs[0].run_id == run1
        assert exp.runs[0].status == "running"

        # pause
        mgr.pause("live_us_ml_v1")
        exp = mgr.get("live_us_ml_v1")
        assert exp.status == "paused"
        assert exp.runs[0].status == "paused"
        assert exp.runs[0].ended_at is not None

        # resume
        run2 = mgr.resume("live_us_ml_v1")
        assert run2 != run1
        exp = mgr.get("live_us_ml_v1")
        assert exp.status == "running"
        assert exp.current_run == run2
        assert len(exp.runs) == 2
        assert exp.runs[1].base_run == run1

        # stop
        mgr.stop("live_us_ml_v1")
        exp = mgr.get("live_us_ml_v1")
        assert exp.status == "completed"
        assert exp.runs[1].status == "completed"

        # archive
        mgr.archive("live_us_ml_v1")
        exp = mgr.get("live_us_ml_v1")
        assert exp.status == "archived"


def test_guard_rejects_invalid_transitions():
    import tempfile, os, pytest
    from live.experiment_manager import ExperimentManager
    with tempfile.TemporaryDirectory() as tmpdir:
        mgr = ExperimentManager(registry_path=os.path.join(tmpdir, "r.json"))
        mgr.register("live", "us", "ml", 1, "cfg.yaml")
        mgr.start("live_us_ml_v1")

        # can't start running experiment
        with pytest.raises(RuntimeError, match="Cannot start"):
            mgr.start("live_us_ml_v1")

        # can't resume running experiment
        with pytest.raises(RuntimeError, match="Cannot resume"):
            mgr.resume("live_us_ml_v1")

        # can't archive running experiment
        with pytest.raises(RuntimeError, match="Cannot archive"):
            mgr.archive("live_us_ml_v1")

        mgr.stop("live_us_ml_v1")

        # can't pause completed
        with pytest.raises(RuntimeError, match="Cannot pause"):
            mgr.pause("live_us_ml_v1")


def test_list_filtering():
    import tempfile, os
    from live.experiment_manager import ExperimentManager
    with tempfile.TemporaryDirectory() as tmpdir:
        mgr = ExperimentManager(registry_path=os.path.join(tmpdir, "r.json"))
        mgr.register("live", "us", "ml", 1, "cfg.yaml")
        mgr.register("paper", "hk", "mom", 1, "cfg2.yaml")
        mgr.start("live_us_ml_v1")
        mgr.start("paper_hk_mom_v1")

        live = mgr.list(_type="live")
        assert len(live) == 1
        assert live[0].id == "live_us_ml_v1"

        running = mgr.list(status="running")
        assert len(running) == 2
```

- [ ] **Step 2: Run all tests**

```bash
cd /opt/quant-prod && PYTHONPATH=/opt/quant-prod .venv/bin/python3 -m pytest tests/test_experiment_manager.py -v
```

Expected: 5 tests PASS

- [ ] **Step 3: Commit**

```bash
git add tests/test_experiment_manager.py
git commit -m "test: ExperimentManager lifecycle + guard tests"
```

### Task 1.3: Add BQ run logging to ExperimentManager

**Files:**
- Modify: `live/experiment_manager.py`

- [ ] **Step 1: Add _log_run_to_bq method**

Append to `ExperimentManager` class in `live/experiment_manager.py`:

```python
    def _log_run_to_bq(self, exp_id: str, run_id: str, status: str,
                       base_run: Optional[str] = None):
        """Write run metadata to BQ experiment_runs table (best-effort)."""
        try:
            from google.cloud import bigquery
            client = bigquery.Client(project="deductive-notch-495015-c2")
            now = datetime.now(timezone.utc).isoformat()
            rows = [{
                "run_id": run_id,
                "exp_id": exp_id,
                "status": status,
                "started_at": now,
                "ended_at": None,
                "base_run": base_run or "",
                "notes": "",
            }]
            errors = client.insert_rows_json(
                "deductive-notch-495015-c2.quant.experiment_runs", rows
            )
            if errors:
                logger.warning("BQ run log errors: %s", errors[:3])
        except Exception as e:
            logger.warning("Failed to log run to BQ (non-fatal): %s", e)

    def _update_run_in_bq(self, exp_id: str, run_id: str, status: str):
        """Update run status in BQ (best-effort, streaming buffer limitation)."""
        # Note: UPDATE may fail on streaming buffer; this is informational only.
        # Dashboard uses experiment_runs for display; exact status comes from registry.
        pass  # Placeholder — full implementation deferred to Phase 4
```

- [ ] **Step 2: Hook _log_run_to_bq into start() and resume()**

In `start()` method, after `_add_run()`:

```python
        self._log_run_to_bq(exp_id, run_id, "running")
```

In `resume()` method, after `_add_run()`:

```python
        self._log_run_to_bq(exp_id, run_id, "running", base_run=prev_run)
```

- [ ] **Step 3: Run tests to ensure no regression**

```bash
cd /opt/quant-prod && PYTHONPATH=/opt/quant-prod .venv/bin/python3 -m pytest tests/test_experiment_manager.py -v
```

Expected: 5 tests PASS (BQ logging fails gracefully in test environment)

- [ ] **Step 4: Commit**

```bash
git add live/experiment_manager.py
git commit -m "feat: BQ run logging in ExperimentManager start/resume"
```

---

## Phase 2: CLI

### Task 2.1: Create CLI module

**Files:**
- Create: `live/exp_cli.py`

- [ ] **Step 1: Write CLI with argparse**

```python
# live/exp_cli.py
"""Experiment CLI — manage experiments from the command line.

Usage:
    python -m live.exp_cli register live/us/ml/2 --config live/configs/exp1_ml_us.yaml
    python -m live.exp_cli start live_us_ml_v2
    python -m live.exp_cli pause live_us_ml_v2
    python -m live.exp_cli resume live_us_ml_v2
    python -m live.exp_cli stop live_us_ml_v2
    python -m live.exp_cli archive live_us_ml_v2
    python -m live.exp_cli list [--type live] [--status running]
    python -m live.exp_cli show live_us_ml_v2
    python -m live.exp_cli runs live_us_ml_v2
"""
import argparse
import json
import sys
from live.experiment_manager import ExperimentManager


def _parse_id(id_str: str) -> dict:
    """Parse 'live/us/ml/2' into components. Also accepts full ID 'live_us_ml_v2'."""
    if "/" in id_str:
        parts = id_str.split("/")
        if len(parts) != 4:
            raise ValueError("Expected format: type/market/strategy/version")
        return {"_type": parts[0], "market": parts[1],
                "strategy": parts[2], "version": int(parts[3])}
    return {"exp_id": id_str}


def cmd_register(mgr: ExperimentManager, args):
    parsed = _parse_id(args.id)
    if "exp_id" in parsed:
        print("ERROR: register requires format: type/market/strategy/version")
        sys.exit(1)
    exp = mgr.register(
        _type=parsed["_type"], market=parsed["market"],
        strategy=parsed["strategy"], version=parsed["version"],
        config_path=args.config, name=args.name or "",
    )
    print(f"Registered: {exp.id} ({exp.status})")


def cmd_start(mgr: ExperimentManager, args):
    run_id = mgr.start(args.id)
    print(f"Started {args.id} → run {run_id}")


def cmd_pause(mgr: ExperimentManager, args):
    mgr.pause(args.id)
    print(f"Paused {args.id}")


def cmd_resume(mgr: ExperimentManager, args):
    run_id = mgr.resume(args.id)
    print(f"Resumed {args.id} → run {run_id}")


def cmd_stop(mgr: ExperimentManager, args):
    mgr.stop(args.id)
    print(f"Stopped {args.id}")


def cmd_archive(mgr: ExperimentManager, args):
    mgr.archive(args.id)
    print(f"Archived {args.id}")


def cmd_list(mgr: ExperimentManager, args):
    exps = mgr.list(_type=args.type, status=args.status)
    if not exps:
        print("No experiments found.")
        return
    for e in exps:
        run_info = f"run={e.current_run}" if e.current_run else "no run"
        print(f"  [{e.status:10s}] {e.id:30s} {run_info}")


def cmd_show(mgr: ExperimentManager, args):
    import json as _json
    exp = mgr.get(args.id)
    d = {
        "id": exp.id, "type": exp.type, "market": exp.market,
        "strategy": exp.strategy, "version": exp.version,
        "status": exp.status, "config_path": exp.config_path,
        "created_at": exp.created_at, "current_run": exp.current_run,
        "name": exp.name,
        "runs": [
            {"run_id": r.run_id, "status": r.status,
             "started": r.started_at, "ended": r.ended_at}
            for r in exp.runs
        ],
    }
    print(_json.dumps(d, indent=2))


def cmd_runs(mgr: ExperimentManager, args):
    runs = mgr.runs(args.id)
    if not runs:
        print("No runs found.")
        return
    for r in runs:
        print(f"  {r.run_id}  [{r.status:10s}]  {r.started_at}  base={r.base_run or '-'}")


COMMANDS = {
    "register": cmd_register,
    "start": cmd_start,
    "pause": cmd_pause,
    "resume": cmd_resume,
    "stop": cmd_stop,
    "archive": cmd_archive,
    "list": cmd_list,
    "show": cmd_show,
    "runs": cmd_runs,
}


def main():
    parser = argparse.ArgumentParser(description="Experiment Manager CLI")
    parser.add_argument("command", choices=list(COMMANDS))
    parser.add_argument("id", nargs="?", help="Experiment ID or type/market/strategy/version")
    parser.add_argument("--config", help="Config YAML path (register only)")
    parser.add_argument("--name", help="Experiment name (register only)")
    parser.add_argument("--type", choices=["live", "paper", "prod"], help="Filter by type")
    parser.add_argument("--status", choices=["pending", "running", "paused", "completed", "archived", "failed"],
                        help="Filter by status")
    parser.add_argument("--registry", default="/var/quant/experiments/registry.json",
                        help="Registry file path")
    args = parser.parse_args()

    mgr = ExperimentManager(registry_path=args.registry)
    fn = COMMANDS[args.command]
    try:
        fn(mgr, args)
    except (ValueError, KeyError, RuntimeError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Smoke test CLI**

```bash
cd /opt/quant-prod && PYTHONPATH=/opt/quant-prod .venv/bin/python3 live/exp_cli.py register live/us/ml/99 --config /tmp/test.yaml --name "Test"
```

Expected: `Registered: live_us_ml_v99 (pending)`

```bash
cd /opt/quant-prod && PYTHONPATH=/opt/quant-prod .venv/bin/python3 live/exp_cli.py start live_us_ml_v99
```

Expected: `Started live_us_ml_v99 → run 20260604_HHMMSS`

```bash
cd /opt/quant-prod && PYTHONPATH=/opt/quant-prod .venv/bin/python3 live/exp_cli.py list
```

Expected: experiment appears with status "running"

```bash
cd /opt/quant-prod && PYTHONPATH=/opt/quant-prod .venv/bin/python3 live/exp_cli.py pause live_us_ml_v99
cd /opt/quant-prod && PYTHONPATH=/opt/quant-prod .venv/bin/python3 live/exp_cli.py resume live_us_ml_v99
cd /opt/quant-prod && PYTHONPATH=/opt/quant-prod .venv/bin/python3 live/exp_cli.py stop live_us_ml_v99
cd /opt/quant-prod && PYTHONPATH=/opt/quant-prod .venv/bin/python3 live/exp_cli.py archive live_us_ml_v99
cd /opt/quant-prod && PYTHONPATH=/opt/quant-prod .venv/bin/python3 live/exp_cli.py show live_us_ml_v99
```

Expected: Each command succeeds, final status = "archived".

- [ ] **Step 3: Clean up test experiment from registry**

```bash
cd /opt/quant-prod && python3 -c "
import json
with open('/var/quant/experiments/registry.json') as f:
    d = json.load(f)
if 'live_us_ml_v99' in d['experiments']:
    del d['experiments']['live_us_ml_v99']
    with open('/var/quant/experiments/registry.json', 'w') as f:
        json.dump(d, f, indent=2)
    print('Cleaned up')
"
```

- [ ] **Step 4: Commit**

```bash
git add live/exp_cli.py
git commit -m "feat: experiment CLI — register/start/pause/resume/stop/archive/list/show/runs"
```

---

## Phase 3: Runner Integration

### Task 3.1: Update experiment yaml format

**Files:**
- Modify: `live/configs/exp1_ml_us.yaml`
- Modify: `live/configs/exp2_momentum_us.yaml`
- Modify: `live/configs/exp3_ml_hk.yaml`
- Modify: `live/configs/exp4_momentum_hk.yaml`

- [ ] **Step 1: Update exp1_ml_us.yaml experiment section**

Replace in `live/configs/exp1_ml_us.yaml`:

```yaml
experiment:
  type: live
  market: us
  strategy: ml
  version: 2
  name: "MLPredStrategy us_tech v2"
```

Remove old `id: exp1_ml_v2` line.

- [ ] **Step 2: Update exp2_momentum_us.yaml experiment section**

Replace:

```yaml
experiment:
  type: live
  market: us
  strategy: mom
  version: 1
  name: "SimpleMomentum US (control)"
```

- [ ] **Step 3: Update exp3_ml_hk.yaml experiment section**

Replace:

```yaml
experiment:
  type: live
  market: hk
  strategy: ml
  version: 3
  name: "MLPredStrategy hk_tech v3"
```

- [ ] **Step 4: Update exp4_momentum_hk.yaml experiment section**

Replace:

```yaml
experiment:
  type: live
  market: hk
  strategy: mom
  version: 2
  name: "SimpleMomentum HK (control)"
```

- [ ] **Step 5: Commit**

```bash
git add live/configs/exp1_ml_us.yaml live/configs/exp2_momentum_us.yaml live/configs/exp3_ml_hk.yaml live/configs/exp4_momentum_hk.yaml
git commit -m "refactor: experiment yaml — new type/market/strategy/version format"
```

### Task 3.2: Update config.py to parse new format

**Files:**
- Modify: `live/config.py`

- [ ] **Step 1: Add experiment ID derivation in _apply_defaults**

In `live/config.py`, find `_apply_defaults()` and add after experiment section parsing:

```python
    # Derive experiment ID from type/market/strategy/version
    exp_cfg = config.get("experiment", {})
    if exp_cfg:
        if "id" not in exp_cfg:
            _type = exp_cfg.get("type", "live")
            market = exp_cfg.get("market", "us")
            strategy = exp_cfg.get("strategy", "ml")
            version = exp_cfg.get("version", 1)
            exp_cfg["id"] = f"{_type}_{market}_{strategy}_v{version}"
```

- [ ] **Step 2: Verify config loading works**

```bash
cd /opt/quant-prod && PYTHONPATH=/opt/quant-prod .venv/bin/python3 -c "
from live.config import load_config
cfg = load_config('live/configs/exp1_ml_us.yaml')
print('Experiment ID:', cfg.get('experiment', {}).get('id'))
"
```

Expected: `Experiment ID: live_us_ml_v2`

- [ ] **Step 3: Commit**

```bash
git add live/config.py
git commit -m "feat: config.py derives experiment ID from type/market/strategy/version"
```

### Task 3.3: Integrate ExperimentManager into LiveRunner

**Files:**
- Modify: `live/runner.py`

- [ ] **Step 1: Add ExperimentManager call in LiveRunner startup**

In `live/runner.py`, in the `LiveRunner.run()` method, after config loading, add:

```python
        # ── Experiment lifecycle integration ──
        exp_cfg = self.config.get("experiment", {})
        exp_id = exp_cfg.get("id", "")
        run_id = None

        if exp_id:
            from live.experiment_manager import ExperimentManager
            mgr = ExperimentManager()
            try:
                exp = mgr.get(exp_id)
                if exp.status == "archived":
                    logger.warning("Experiment %s is archived, skipping lifecycle", exp_id)
                else:
                    run_id = mgr.start(exp_id)
                    logger.info("Experiment %s started → run %s", exp_id, run_id)
                    self.config["_run_id"] = run_id
            except KeyError:
                # Not registered — auto-register
                _type = exp_cfg.get("type", "live")
                market = exp_cfg.get("market", "us")
                strategy = exp_cfg.get("strategy", "ml")
                version = exp_cfg.get("version", 1)
                config_path = getattr(self, '_config_path', '')
                mgr.register(_type, market, strategy, version, config_path,
                             name=exp_cfg.get("name", ""))
                run_id = mgr.start(exp_id)
                logger.info("Auto-registered %s → run %s", exp_id, run_id)
                self.config["_run_id"] = run_id
```

- [ ] **Step 2: Add cleanup on run completion/error**

At the end of `run()` and on exception, add:

```python
        # ── Experiment lifecycle cleanup ──
        if exp_id and run_id:
            from live.experiment_manager import ExperimentManager
            mgr = ExperimentManager()
            try:
                exp = mgr.get(exp_id)
                if exp.status == "running":
                    mgr.stop(exp_id)
                    logger.info("Experiment %s stopped (run %s completed)", exp_id, run_id)
            except Exception:
                pass
```

On fatal error (before re-raise):

```python
            if exp_id:
                mgr = ExperimentManager()
                try:
                    mgr.fail(exp_id, notes=str(e))
                except Exception:
                    pass
```

- [ ] **Step 3: Pass run_id to DashboardObserver**

Find where `DashboardObserver.record_equity()` is called in `live/runner.py` (around line 1129) and add `run_id` to the call:

```python
                    self._dash_observer.record_equity(
                        bar=self._live_bar_count,
                        equity=eq,
                        cash=portfolio.cash,
                        portfolio_value=eq,
                        daily_pnl=eq - self._live_daily_start_equity if self._live_daily_start_equity > 0 else 0,
                        drawdown=current_dd,
                        run_id=run_id or self.config.get("_run_id", ""),
                    )
```

- [ ] **Step 4: Commit**

```bash
git add live/runner.py
git commit -m "feat: LiveRunner integrates ExperimentManager for lifecycle + run_id"
```

### Task 3.4: Update DashboardObserver to write run_id

**Files:**
- Modify: `dashboard/observer.py`

- [ ] **Step 1: Add run_id to record_equity and record_trade**

In `dashboard/observer.py`, update `record_equity()` signature:

```python
    def record_equity(self, bar: int, equity: float, cash: float,
                      portfolio_value: float, daily_pnl: float,
                      drawdown: float, run_id: str = ""):
        ...
        row = {
            ...
            "run_id": run_id,
        }
```

Similarly for `record_trade()`:

```python
    def record_trade(self, bar: int, symbol: str, side: str,
                     qty: float, price: float, commission: float = 0,
                     run_id: str = ""):
        ...
        row = {
            ...
            "run_id": run_id,
        }
```

- [ ] **Step 2: Verify existing code still compiles**

```bash
cd /opt/quant-prod && PYTHONPATH=/opt/quant-prod .venv/bin/python3 -c "from dashboard.observer import DashboardObserver; print('OK')"
```

Expected: OK

- [ ] **Step 3: Commit**

```bash
git add dashboard/observer.py
git commit -m "feat: DashboardObserver writes run_id to BQ"
```

---

## Phase 4: Dashboard

### Task 4.1: Add type filtering to API

**Files:**
- Modify: `dashboard/server.py`

- [ ] **Step 1: Add type query parameter to /api/experiments**

Modify the `/api/experiments` endpoint (find near line 100):

```python
@app.get("/api/experiments")
async def experiments(_type: str = ""):
    """Return latest equity snapshots, optionally filtered by experiment type."""
    client = _get_bq()
    prefix_filter = ""
    if _type:
        prefix_filter = f"AND exp_id LIKE '{_type}_%'"
    query = f"""
        SELECT * FROM (
          SELECT *, ROW_NUMBER() OVER (
            PARTITION BY exp_id ORDER BY ts DESC
          ) AS rn
          FROM {_table("experiment_equity")}
        )
        WHERE rn = 1 {prefix_filter}
    """
    ...
```

- [ ] **Step 2: Add run_id filter to equity and trades endpoints**

Modify `/api/equity/{exp_id}` and `/api/trades/{exp_id}` to accept optional `run_id` query param:

```python
@app.get("/api/equity/{exp_id}")
async def equity(exp_id: str, run_id: str = ""):
    run_filter = f"AND run_id = '{run_id}'" if run_id else ""
    query = f"""
        SELECT ts, bar, equity, cash, portfolio_value, daily_pnl, drawdown, run_id
        FROM {_table("experiment_equity")}
        WHERE exp_id = '{exp_id}' {run_filter}
        ORDER BY bar
    """
    ...
```

Same pattern for `/api/trades/{exp_id}` and `/api/experiments/{exp_id}/positions`.

- [ ] **Step 3: Add run list endpoint**

```python
@app.get("/api/experiments/{exp_id}/runs")
async def experiment_runs(exp_id: str):
    client = _get_bq()
    query = f"""
        SELECT run_id, status, started_at, ended_at, base_run
        FROM {_table("experiment_runs")}
        WHERE exp_id = '{exp_id}'
        ORDER BY started_at DESC
    """
    rows = client.query(query).result()
    return [_row_to_dict(r, ["run_id", "status", "started_at", "ended_at", "base_run"]) for r in rows]
```

- [ ] **Step 4: Commit**

```bash
git add dashboard/server.py
git commit -m "feat: Dashboard API — type filter + run_id filter + runs endpoint"
```

### Task 4.2: Dashboard frontend Tab restructuring

**Files:**
- Modify: `dashboard/index.html`

- [ ] **Step 1: Restructure tabs array**

Find `const tabs` (near line 815) and replace:

```javascript
const tabs = [
  { id: 'overview', label: 'Overview' },
  { id: 'live', label: 'Live' },
  { id: 'paper', label: 'Paper Run' },
  { id: 'prod', label: 'Prod' },
  { id: 'pipeline', label: 'Pipeline' },
  { id: 'alerts', label: 'Alerts' },
];
```

Remove the old individual experiment tab logic (the `liveExperiment` tab).

- [ ] **Step 2: Create Live Tab content block**

Replace the old Live Experiment content section (div with `v-if="activeTab === 'liveExperiment'"`) with a type-filtered version:

```html
    <!-- ═══════ LIVE TAB ═══════ -->
    <div v-if="activeTab === 'live'">
      <div style="margin-bottom:16px">
        <select v-model="selectedLiveExp" @change="loadLiveExperiment" 
                style="background:var(--bg-card);color:var(--text);border:1px solid var(--border);border-radius:4px;padding:6px 10px;">
          <option value="">— Select Experiment —</option>
          <option v-for="e in liveExperiments" :value="e.exp_id">{{ e.exp_id }} ({{ e.status }})</option>
        </select>
        <select v-if="selectedLiveExp" v-model="selectedRun" @change="loadLiveExperiment"
                style="margin-left:8px;background:var(--bg-card);color:var(--text);border:1px solid var(--border);border-radius:4px;padding:6px 10px;">
          <option value="">— Latest Run —</option>
          <option v-for="r in currentRuns" :value="r.run_id">{{ r.run_id }} [{{ r.status }}]</option>
        </select>
      </div>
      <!-- Equity chart + positions + trades (same as existing LiveExperiment content) -->
      <!-- ... existing chart divs and tables ... -->
    </div>
```

- [ ] **Step 3: Add data loading for type-filtered experiments**

Add to script section:

```javascript
    const liveExperiments = ref([]);
    const paperExperiments = ref([]);
    const prodExperiments = ref([]);
    const selectedLiveExp = ref('');
    const selectedPaperExp = ref('');
    const selectedProdExp = ref('');
    const selectedRun = ref('');
    const currentRuns = ref([]);

    async function loadTypedExperiments() {
      const all = await fetchJSON('/api/experiments');
      const meta = await fetchJSON('/api/experiments/meta');
      if (!all || !meta) return;

      const bqMap = {};
      all.forEach(e => { bqMap[e.exp_id] = e; });

      const enrich = (m) => ({
        ...m,
        ...(bqMap[m.exp_id] || { bar: 0, equity: 0, cash: 0, daily_pnl: 0, drawdown: 0 }),
      });

      liveExperiments.value = meta.filter(m => m.exp_id.startsWith('live_')).map(enrich);
      paperExperiments.value = meta.filter(m => m.exp_id.startsWith('paper_')).map(enrich);
      prodExperiments.value = meta.filter(m => m.exp_id.startsWith('prod_')).map(enrich);
    }

    async function loadLiveExperiment() {
      if (!selectedLiveExp.value) return;
      const runParam = selectedRun.value ? `?run_id=${selectedRun.value}` : '';
      equityData.value = await fetchJSON(`/api/equity/${selectedLiveExp.value}${runParam}`) || [];
      trades.value = await fetchJSON(`/api/trades/${selectedLiveExp.value}${runParam}`) || [];
      positions.value = await fetchJSON(`/api/experiments/${selectedLiveExp.value}/positions`) || [];
      currentRuns.value = await fetchJSON(`/api/experiments/${selectedLiveExp.value}/runs`) || [];
      renderEquityChart();
      renderDrawdownChart();
    }
```

- [ ] **Step 4: Wire up Paper Run and Prod tabs similarly**

Paper Run tab reuses existing paper run detail logic but filters to `paper_*` experiments. Prod tab is read-only (no pause/resume controls shown).

- [ ] **Step 5: Update refresh to call loadTypedExperiments**

```javascript
    async function refresh() {
      await Promise.all([
        loadTypedExperiments(),
        loadPipeline(),
      ]);
    }
    refresh();
    setInterval(refresh, 30000);
```

- [ ] **Step 6: Commit**

```bash
git add dashboard/index.html
git commit -m "feat: Dashboard tabs — Live/PaperRun/Prod type-isolated with run selector"
```

---

## Phase 5: Migration

### Task 5.1: Migration script

**Files:**
- Create: `scripts/migrate_experiments.py`

- [ ] **Step 1: Write migration script**

```python
"""Migrate existing experiments to new ExperimentManager system.

Registers 4 live experiments, backfills run_id in BQ,
and writes experiment_runs metadata.
"""
import json
import sys
from datetime import datetime, timezone
from google.cloud import bigquery

PROJECT = "deductive-notch-495015-c2"
REGISTRY = "/var/quant/experiments/registry.json"

MIGRATIONS = [
    {"old_id": "exp1_ml_v2",      "type": "live", "market": "us", "strategy": "ml",  "version": 2,
     "name": "MLPredStrategy us_tech v2", "config": "live/configs/exp1_ml_us.yaml"},
    {"old_id": "exp2_simple_momentum", "type": "live", "market": "us", "strategy": "mom", "version": 1,
     "name": "SimpleMomentum US (control)", "config": "live/configs/exp2_momentum_us.yaml"},
    {"old_id": "exp3_ml_hk",      "type": "live", "market": "hk", "strategy": "ml",  "version": 3,
     "name": "MLPredStrategy hk_tech v3", "config": "live/configs/exp3_ml_hk.yaml"},
    {"old_id": "exp4_momentum_hk","type": "live", "market": "hk", "strategy": "mom", "version": 2,
     "name": "SimpleMomentum HK (control)", "config": "live/configs/exp4_momentum_hk.yaml"},
]


def main():
    bq = bigquery.Client(project=PROJECT)

    # Load or create registry
    try:
        with open(REGISTRY) as f:
            reg = json.load(f)
    except FileNotFoundError:
        reg = {"experiments": {}}

    for m in MIGRATIONS:
        new_id = f"{m['type']}_{m['market']}_{m['strategy']}_v{m['version']}"
        print(f"\n=== {m['old_id']} → {new_id} ===")

        # 1. Register in registry
        if new_id in reg["experiments"]:
            print(f"  SKIP: already registered")
        else:
            now = datetime.now(timezone.utc).isoformat()
            reg["experiments"][new_id] = {
                "id": new_id,
                "type": m["type"],
                "market": m["market"],
                "strategy": m["strategy"],
                "version": m["version"],
                "status": "running",
                "config_path": m["config"],
                "created_at": now,
                "name": m["name"],
                "current_run": None,
                "runs": [],
            }
            print(f"  REGISTERED: {new_id}")

        # 2. Count old data rows
        eq = bq.query(
            f"SELECT COUNT(*) AS cnt FROM quant.experiment_equity WHERE exp_id='{m['old_id']}'"
        ).result().to_dataframe()["cnt"][0]
        tr = bq.query(
            f"SELECT COUNT(*) AS cnt FROM quant.experiment_trades WHERE exp_id='{m['old_id']}'"
        ).result().to_dataframe()["cnt"][0]
        print(f"  Old data: {eq} equity rows, {tr} trade rows")

        # 3. Backfill run_id in BQ (use a migration run_id)
        run_id = f"migrate_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
        print(f"  Backfilling run_id={run_id} to BQ...")

        try:
            bq.query(
                f"UPDATE quant.experiment_equity SET run_id='{run_id}' "
                f"WHERE exp_id='{m['old_id']}' AND run_id IS NULL"
            ).result()
        except Exception as e:
            print(f"  ⚠️  equity UPDATE failed (streaming buffer?): {e}")

        try:
            bq.query(
                f"UPDATE quant.experiment_trades SET run_id='{run_id}' "
                f"WHERE exp_id='{m['old_id']}' AND run_id IS NULL"
            ).result()
        except Exception as e:
            print(f"  ⚠️  trades UPDATE failed (streaming buffer?): {e}")

        # 4. NOTE: We do NOT change exp_id in BQ because:
        #    - Streaming buffer blocks UPDATE/DELETE
        #    - Old data under old exp_id is harmless (Dashboard filters by new ID)
        #    - New runs write under new exp_id going forward

    # Save registry
    with open(REGISTRY, 'w') as f:
        json.dump(reg, f, indent=2)
    print(f"\n✅ Registry saved to {REGISTRY}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run migration (dry-run first)**

```bash
cd /opt/quant-prod && PYTHONPATH=/opt/quant-prod .venv/bin/python3 scripts/migrate_experiments.py
```

Expected: 4 experiments registered, BQ backfill attempted (may warn about streaming buffer).

- [ ] **Step 3: Verify registry**

```bash
cd /opt/quant-prod && PYTHONPATH=/opt/quant-prod .venv/bin/python3 live/exp_cli.py list
```

Expected: 4 experiments listed with status "running".

- [ ] **Step 4: Commit**

```bash
git add scripts/migrate_experiments.py
git commit -m "feat: experiment migration script — register 4 live experiments"
```

---

## Phase 6: Integration Test

### Task 6.1: End-to-end smoke test

- [ ] **Step 1: Verify ExperimentManager + CLI + Runner integration**

```bash
cd /opt/quant-prod && PYTHONPATH=/opt/quant-prod .venv/bin/python3 -c "
from live.experiment_manager import ExperimentManager
from live.config import load_config

# Load config and verify ID derivation
cfg = load_config('live/configs/exp1_ml_us.yaml')
exp_id = cfg['experiment']['id']
assert exp_id == 'live_us_ml_v2', f'Expected live_us_ml_v2, got {exp_id}'

# Registry check
mgr = ExperimentManager()
exp = mgr.get(exp_id)
assert exp.status == 'running' or exp.status == 'pending'
print(f'✅ {exp_id}: {exp.status}, runs={len(exp.runs)}')
"
```

Expected: ✅ — experiment found with valid status

- [ ] **Step 2: Verify BQ schema readiness**

```bash
bq query --use_legacy_sql=false \
  "SELECT column_name FROM deductive-notch-495015-c2.quant.INFORMATION_SCHEMA.COLUMNS WHERE table_name IN ('experiment_equity', 'experiment_trades') AND column_name='run_id'"
```

Expected: 2 rows returned (run_id column exists in both tables)

- [ ] **Step 3: Run full test suite**

```bash
cd /opt/quant-prod && PYTHONPATH=/opt/quant-prod .venv/bin/python3 -m pytest tests/ -v --tb=short
```

Expected: All tests PASS (at minimum experiment_manager tests)

- [ ] **Step 4: Commit and push**

```bash
git add -A && git commit -m "chore: end-to-end integration test passing"
```

---

## Summary

| Phase | Tasks | Files |
|-------|-------|-------|
| 0: BQ Schema | 1 | 3 BQ tables |
| 1: Core | 3 | experiment_manager.py + tests + BQ logging |
| 2: CLI | 1 | exp_cli.py |
| 3: Runner | 4 | 4 yamls + config.py + runner.py + observer.py |
| 4: Dashboard | 2 | server.py + index.html |
| 5: Migration | 1 | migrate_experiments.py |
| 6: Integration | 1 | smoke test |

**Total: 13 tasks, 7 new files, 7 modified files, 3 BQ tables**
