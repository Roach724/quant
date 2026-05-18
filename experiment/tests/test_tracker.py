"""Tests for ExperimentTracker."""
import tempfile
import json
import shutil
from pathlib import Path
from experiment.tracker import ExperimentTracker


def test_register_experiment():
    """Register creates directory, JSON, and INDEX.md entry."""
    with tempfile.TemporaryDirectory() as td:
        tracker = ExperimentTracker(base_dir=td)
        exp_dir = tracker.register_experiment(
            "exp_001", "Test Experiment",
            hypothesis="This is a test",
            changes=["added test"]
        )
        assert exp_dir.exists()
        assert (exp_dir / "experiment.json").exists()
        assert (exp_dir / "investment_sessions.json").exists()
        meta = tracker.get_experiment("exp_001")
        assert meta["name"] == "Test Experiment"
        assert meta["hypothesis"] == "This is a test"


def test_update_results():
    """Update adds results to experiment JSON."""
    with tempfile.TemporaryDirectory() as td:
        tracker = ExperimentTracker(base_dir=td)
        tracker.register_experiment("exp_001", "Test")
        tracker.update_results("exp_001", {"sharpe": 1.5}, verdict="improved")
        meta = tracker.get_experiment("exp_001")
        assert meta["results"]["sharpe"] == 1.5
        assert meta["verdict"] == "improved"
        assert meta["status"] == "completed"


def test_record_session():
    """Recording a session appends to sessions JSON."""
    with tempfile.TemporaryDirectory() as td:
        tracker = ExperimentTracker(base_dir=td)
        tracker.register_experiment("exp_001", "Test")
        tracker.record_session(
            "exp_001", "20260518_paper_001", "paper_trading", "/tmp/test"
        )
        sessions = json.loads(
            (Path(td) / "exp_001" / "investment_sessions.json").read_text()
        )
        assert len(sessions) == 1
        assert sessions[0]["session_id"] == "20260518_paper_001"


def test_list_experiments():
    """List returns all registered experiments."""
    with tempfile.TemporaryDirectory() as td:
        tracker = ExperimentTracker(base_dir=td)
        tracker.register_experiment("exp_001", "A")
        tracker.register_experiment("exp_002", "B")
        exps = tracker.list_experiments()
        assert len(exps) == 2


def test_compare_generates_report():
    """Compare returns Markdown with metric table."""
    with tempfile.TemporaryDirectory() as td:
        tracker = ExperimentTracker(base_dir=td)
        tracker.register_experiment("exp_001", "Baseline")
        tracker.update_results(
            "exp_001", {"sharpe": 1.2, "total_return": 0.15}
        )
        tracker.register_experiment("exp_002", "Improved")
        tracker.update_results(
            "exp_002", {"sharpe": 1.8, "total_return": 0.25}
        )
        report = tracker.compare("exp_001", "exp_002")
        assert "Baseline" in report
        assert "Improved" in report
        assert "1.2" in report or "1.2000" in report


def test_index_md_created():
    """INDEX.md is created on init."""
    with tempfile.TemporaryDirectory() as td:
        tracker = ExperimentTracker(base_dir=td)
        assert (Path(td) / "INDEX.md").exists()


def test_delete_experiment():
    """Delete removes directory and INDEX.md entry."""
    with tempfile.TemporaryDirectory() as td:
        tracker = ExperimentTracker(base_dir=td)
        tracker.register_experiment("exp_001", "Test")
        tracker.delete_experiment("exp_001")
        assert not (Path(td) / "exp_001").exists()
