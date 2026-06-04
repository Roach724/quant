"""Tests for live/experiment_manager.py — ExperimentManager lifecycle.

Uses tempfile.TemporaryDirectory for isolated registry paths
so tests never touch the production registry at /var/quant/experiments/.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from live.experiment_manager import ExperimentManager, _build_id


# ── Fixture ──────────────────────────────────────────────────────────

@pytest.fixture
def mgr():
    """ExperimentManager backed by a temp registry file."""
    with tempfile.TemporaryDirectory() as tmpdir:
        registry = str(Path(tmpdir) / "registry.json")
        yield ExperimentManager(registry_path=registry)


# ── ID helpers ───────────────────────────────────────────────────────

def test_build_id():
    assert _build_id("live", "us", "ml", 2) == "live_us_ml_v2"
    assert _build_id("paper", "hk", "mom", 1) == "paper_hk_mom_v1"
    assert _build_id("prod", "crypto", "arb", 5) == "prod_crypto_arb_v5"


# ── Registration ─────────────────────────────────────────────────────

def test_register_creates_entry(mgr):
    """Register creates an entry; id, status, and JSON file are correct."""
    exp_id = mgr.register("live", "us", "ml", 2, "live/configs/exp1_ml_us.yaml",
                           name="ML US v2")
    assert exp_id == "live_us_ml_v2"

    exp = mgr.get(exp_id)
    assert exp.id == exp_id
    assert exp.type == "live"
    assert exp.market == "us"
    assert exp.strategy == "ml"
    assert exp.version == 2
    assert exp.status == "pending"
    assert exp.config_path == "live/configs/exp1_ml_us.yaml"
    assert exp.name == "ML US v2"
    assert exp.current_run is None
    assert exp.runs == []
    assert exp.created_at  # non-empty ISO string

    # Verify JSON file on disk
    assert mgr._path.exists()
    raw = json.loads(mgr._path.read_text())
    assert exp_id in raw["experiments"]
    assert raw["experiments"][exp_id]["status"] == "pending"


def test_register_duplicate_rejected(mgr):
    """Registering the same id twice raises ValueError."""
    mgr.register("live", "us", "ml", 2, "live/configs/exp1_ml_us.yaml")
    with pytest.raises(ValueError, match="already registered"):
        mgr.register("live", "us", "ml", 2, "live/configs/another.yaml")


def test_register_invalid_type(mgr):
    """Invalid type raises ValueError."""
    with pytest.raises(ValueError, match="Invalid type"):
        mgr.register("invalid", "us", "ml", 1, "config.yaml")


def test_register_invalid_market(mgr):
    """Invalid market raises ValueError."""
    with pytest.raises(ValueError, match="Invalid market"):
        mgr.register("live", "cn", "ml", 1, "config.yaml")


def test_register_invalid_version(mgr):
    """Version must be a positive int."""
    with pytest.raises(ValueError, match="version must be a positive int"):
        mgr.register("live", "us", "ml", 0, "config.yaml")

    with pytest.raises(ValueError, match="version must be a positive int"):
        mgr.register("live", "us", "ml", -1, "config.yaml")


# ── State guards — properties on Experiment ─────────────────────────

def test_can_start_states():
    """can_start: pending/paused/completed/archived → True; running/failed → False."""
    from live.experiment_manager import Experiment

    for status in ("pending", "paused", "completed", "archived"):
        exp = Experiment("x", "live", "us", "ml", 1, status, "cfg.yaml", "2026-01-01T00:00:00Z")
        assert exp.can_start is True, f"status={status} should allow start"

    for status in ("running", "failed"):
        exp = Experiment("x", "live", "us", "ml", 1, status, "cfg.yaml", "2026-01-01T00:00:00Z")
        assert exp.can_start is False, f"status={status} should NOT allow start"


def test_can_pause_resume_stop_archive():
    """Test each guard property with relevant states."""
    from live.experiment_manager import Experiment

    def _make(status):
        return Experiment("x", "live", "us", "ml", 1, status, "cfg.yaml", "2026-01-01T00:00:00Z")

    # can_pause
    assert _make("running").can_pause is True
    assert _make("paused").can_pause is False
    assert _make("completed").can_pause is False

    # can_resume
    assert _make("paused").can_resume is True
    assert _make("running").can_resume is False
    assert _make("completed").can_resume is False

    # can_stop
    assert _make("running").can_stop is True
    assert _make("paused").can_stop is True
    assert _make("pending").can_stop is False
    assert _make("completed").can_stop is False

    # can_archive
    assert _make("completed").can_archive is True
    assert _make("failed").can_archive is True
    assert _make("running").can_archive is False
    assert _make("archived").can_archive is False


# ── Full lifecycle test ──────────────────────────────────────────────

def test_full_lifecycle(mgr):
    """register → start → pause → resume → stop → archive"""
    exp_id = mgr.register("live", "us", "ml", 2, "live/configs/exp_ml_us.yaml",
                           name="Full Lifecycle Test")

    # ── Start ──
    run1 = mgr.start(exp_id)
    assert run1  # non-empty run_id
    exp = mgr.get(exp_id)
    assert exp.status == "running"
    assert exp.current_run == run1
    assert len(exp.runs) == 1
    assert exp.runs[0].run_id == run1
    assert exp.runs[0].status == "running"
    assert exp.runs[0].ended_at is None
    assert exp.runs[0].base_run is None

    # ── Pause ──
    mgr.pause(exp_id)
    exp = mgr.get(exp_id)
    assert exp.status == "paused"
    assert exp.current_run == run1  # unchanged on pause
    assert len(exp.runs) == 1
    assert exp.runs[0].status == "paused"
    assert exp.runs[0].ended_at is not None  # pause sets ended_at

    # ── Resume ──
    run2 = mgr.resume(exp_id)
    assert run2 != run1, "resume must create a new run_id"
    exp = mgr.get(exp_id)
    assert exp.status == "running"
    assert exp.current_run == run2
    assert len(exp.runs) == 2
    assert exp.runs[1].run_id == run2
    assert exp.runs[1].status == "running"
    assert exp.runs[1].base_run == run1  # chained to previous

    # ── Stop ──
    mgr.stop(exp_id)
    exp = mgr.get(exp_id)
    assert exp.status == "completed"
    assert len(exp.runs) == 2
    assert exp.runs[1].status == "completed"
    assert exp.runs[1].ended_at is not None

    # Check that run1 remained paused (not overwritten by stop)
    assert exp.runs[0].status == "paused"

    # ── Archive ──
    mgr.archive(exp_id)
    exp = mgr.get(exp_id)
    assert exp.status == "archived"


# ── Guard rejects invalid transitions ────────────────────────────────

def test_guard_rejects_invalid_transitions(mgr):
    """Each operation must reject invalid state transitions with RuntimeError."""
    exp_id = mgr.register("live", "us", "ml", 1, "config.yaml")

    # Cannot pause a pending experiment
    with pytest.raises(RuntimeError, match="Cannot pause"):
        mgr.pause(exp_id)

    # Cannot resume a pending experiment
    with pytest.raises(RuntimeError, match="Cannot resume"):
        mgr.resume(exp_id)

    # Cannot stop a pending experiment
    with pytest.raises(RuntimeError, match="Cannot stop"):
        mgr.stop(exp_id)

    # Cannot archive a pending experiment
    with pytest.raises(RuntimeError, match="Cannot archive"):
        mgr.archive(exp_id)

    # Start → running
    mgr.start(exp_id)

    # Cannot start again while running
    with pytest.raises(RuntimeError, match="Cannot start"):
        mgr.start(exp_id)

    # Cannot resume while running
    with pytest.raises(RuntimeError, match="Cannot resume"):
        mgr.resume(exp_id)

    # Cannot archive while running
    with pytest.raises(RuntimeError, match="Cannot archive"):
        mgr.archive(exp_id)

    # Stop → completed
    mgr.stop(exp_id)

    # Cannot pause after stop
    with pytest.raises(RuntimeError, match="Cannot pause"):
        mgr.pause(exp_id)

    # Cannot resume after stop
    with pytest.raises(RuntimeError, match="Cannot resume"):
        mgr.resume(exp_id)

    # Start from completed is allowed (replay)
    mgr.start(exp_id)


# ── List filtering ──────────────────────────────────────────────────

def test_list_filtering(mgr):
    """list() with type and status filters returns correct subsets."""
    e1 = mgr.register("live", "us", "ml", 1, "cfg1.yaml", name="Live ML")
    e2 = mgr.register("paper", "hk", "mom", 1, "cfg2.yaml", name="Paper Mom")

    # Before starting, both are pending
    all_exps = mgr.list()
    assert len(all_exps) == 2

    by_type = mgr.list(exp_type="live")
    assert len(by_type) == 1
    assert by_type[0].id == e1

    by_status_pending = mgr.list(status="pending")
    assert len(by_status_pending) == 2

    by_type_paper = mgr.list(exp_type="paper")
    assert len(by_type_paper) == 1
    assert by_type_paper[0].id == e2

    # Start both
    mgr.start(e1)
    mgr.start(e2)

    running = mgr.list(status="running")
    assert len(running) == 2

    live_running = mgr.list(exp_type="live", status="running")
    assert len(live_running) == 1
    assert live_running[0].id == e1

    paper_running = mgr.list(exp_type="paper", status="running")
    assert len(paper_running) == 1
    assert paper_running[0].id == e2


# ── Runs accessor ───────────────────────────────────────────────────

def test_runs_accessor(mgr):
    """runs() returns run history in correct order."""
    exp_id = mgr.register("live", "us", "ml", 1, "cfg.yaml")
    r1 = mgr.start(exp_id)
    mgr.pause(exp_id)
    r2 = mgr.resume(exp_id)
    mgr.stop(exp_id)

    runs = mgr.runs(exp_id)
    assert len(runs) == 2
    assert runs[0].run_id == r1
    assert runs[0].status == "paused"
    assert runs[1].run_id == r2
    assert runs[1].base_run == r1
    assert runs[1].status == "completed"


def test_runs_unknown_experiment(mgr):
    """runs() raises KeyError for unknown experiment."""
    with pytest.raises(KeyError, match="not found"):
        mgr.runs("nonexistent_id")


def test_get_unknown_experiment(mgr):
    """get() raises KeyError for unknown experiment."""
    with pytest.raises(KeyError, match="not found"):
        mgr.get("nonexistent_id")


# ── Fail lifecycle ──────────────────────────────────────────────────

def test_fail_transition(mgr):
    """fail() marks experiment as failed and transitions to failed status."""
    exp_id = mgr.register("live", "us", "ml", 1, "cfg.yaml")
    mgr.start(exp_id)
    mgr.fail(exp_id, notes="Connection timeout")

    exp = mgr.get(exp_id)
    assert exp.status == "failed"
    assert len(exp.runs) == 1
    assert exp.runs[0].status == "failed"
    assert exp.runs[0].ended_at is not None

    # Can archive from failed
    mgr.archive(exp_id)
    assert mgr.get(exp_id).status == "archived"


# ── Resume chain (multiple pause/resume cycles) ─────────────────────

def test_multiple_resume_chain(mgr):
    """pause → resume → pause → resume creates a chain of runs."""
    exp_id = mgr.register("live", "us", "ml", 1, "cfg.yaml")

    r1 = mgr.start(exp_id)
    mgr.pause(exp_id)

    r2 = mgr.resume(exp_id)
    assert r2 != r1
    mgr.pause(exp_id)

    r3 = mgr.resume(exp_id)
    assert r3 not in (r1, r2)

    exp = mgr.get(exp_id)
    assert len(exp.runs) == 3
    assert exp.runs[0].base_run is None
    assert exp.runs[1].base_run == r1
    assert exp.runs[2].base_run == r2


# ── Corrupt JSON recovery ────────────────────────────────────────────

def test_corrupt_registry_recovery(tmp_path):
    """If the registry file contains invalid JSON, start fresh."""
    registry = tmp_path / "bad.json"
    registry.parent.mkdir(parents=True, exist_ok=True)
    registry.write_text("this is not json {{{")

    mgr = ExperimentManager(registry_path=str(registry))
    # Should load without error, starting fresh
    exp_id = mgr.register("live", "us", "ml", 1, "cfg.yaml")

    # Verify a valid JSON was written
    raw = json.loads(registry.read_text())
    assert exp_id in raw["experiments"]


# ── BQ logging is best-effort, never raises ─────────────────────────

def test_bq_logging_never_raises(mgr):
    """start/resume must succeed even without BQ credentials.

    The _log_run_to_bq method catches ALL exceptions internally.
    This test just ensures the lifecycle methods work in a test env
    (no GCP credentials) without throwing.
    """
    exp_id = mgr.register("live", "us", "ml", 1, "cfg.yaml")
    run_id = mgr.start(exp_id)
    assert run_id
    assert mgr.get(exp_id).status == "running"

    mgr.pause(exp_id)
    run_id2 = mgr.resume(exp_id)
    assert run_id2
    assert run_id2 != run_id
