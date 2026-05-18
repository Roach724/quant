"""Tests for ExperimentRunner"""

import os
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd

from experiment.runner import ExperimentRunner
from engine.data import DataFrameSource
from engine.engine import Engine
from engine.config import BacktestConfig
from engine.strategy import Strategy


def test_run_from_engine_result():
    """Verify ExperimentRunner can record an engine backtest result."""
    # Create a simple engine result
    np.random.seed(42)
    dates = pd.date_range("2024-01-01", periods=30, freq="B")
    prices = 100 + np.cumsum(np.random.randn(30) * 0.5)
    close = pd.DataFrame({"AAPL": prices}, index=dates)
    data = DataFrameSource(close=close)

    class BuyHoldStrategy(Strategy):
        def on_bar(self, ctx, bar):
            if bar == 0:
                from engine.strategy import Signal
                return [Signal.buy("AAPL", weight=1.0)]
            return []

    config = BacktestConfig(initial_capital=100000)
    strategy = BuyHoldStrategy()
    result = Engine(config).run(strategy, data)

    # Run experiment
    with tempfile.TemporaryDirectory() as td:
        runner = ExperimentRunner(
            experiments_dir=os.path.join(td, "experiments"),
            investments_dir=os.path.join(td, "investments"),
        )
        meta = runner.run_from_engine_result(
            exp_id="exp_test_001",
            name="Test Buy & Hold",
            hypothesis="Buy hold generates positive returns",
            engine_result=result,
        )

        # Verify
        assert meta["experiment_id"] == "exp_test_001"
        assert meta["status"] == "completed"
        assert "total_return" in meta.get("results", {})

        # Verify files exist
        exp_dir = Path(td) / "experiments" / "exp_test_001"
        assert exp_dir.exists()
        assert (exp_dir / "experiment.json").exists()

        # Verify investment session was created
        sessions = runner.tracker.get_experiment("exp_test_001")
        sessions_json = exp_dir / "investment_sessions.json"
        assert sessions_json.exists()

        # Verify session was recorded
        import json
        with open(sessions_json) as f:
            stored_sessions = json.load(f)
        assert len(stored_sessions) == 1
        assert stored_sessions[0]["type"] == "backtest"

        # Verify investment files were saved
        inv_dir = Path(td) / "investments"
        assert any(inv_dir.iterdir()), "Investment records should exist"


def test_runner_creates_directories():
    """Runner creates required directories."""
    with tempfile.TemporaryDirectory() as td:
        runner = ExperimentRunner(
            experiments_dir=os.path.join(td, "x"),
            investments_dir=os.path.join(td, "y"),
        )
        assert os.path.isdir(os.path.join(td, "x"))
        assert os.path.isdir(os.path.join(td, "y"))


def test_run_full_experiment():
    """Verify run_full_experiment registers experiment and returns metadata."""
    with tempfile.TemporaryDirectory() as td:
        runner = ExperimentRunner(
            experiments_dir=os.path.join(td, "experiments"),
            investments_dir=os.path.join(td, "investments"),
        )
        meta = runner.run_full_experiment(
            exp_id="exp_test_002",
            name="Full Pipeline Test",
            hypothesis="Just testing the pipeline",
            changes=["Initial setup"],
        )
        assert meta["experiment_id"] == "exp_test_002"
        assert meta["name"] == "Full Pipeline Test"
        assert meta["duration_seconds"] > 0

        # Verify files
        exp_dir = Path(td) / "experiments" / "exp_test_002"
        assert exp_dir.exists()
        assert (exp_dir / "experiment.json").exists()

        # Verify status
        exp = runner.tracker.get_experiment("exp_test_002")
        assert exp["status"] == "completed"


def test_runner_allows_multiple_experiments():
    """Runner supports multiple separate experiments."""
    with tempfile.TemporaryDirectory() as td:
        runner = ExperimentRunner(
            experiments_dir=os.path.join(td, "experiments"),
            investments_dir=os.path.join(td, "investments"),
        )
        np.random.seed(42)
        dates = pd.date_range("2024-01-01", periods=10, freq="B")
        prices = 100 + np.cumsum(np.random.randn(10) * 0.5)
        close = pd.DataFrame({"AAPL": prices}, index=dates)
        data = DataFrameSource(close=close)

        class BuyHoldStrategy(Strategy):
            def on_bar(self, ctx, bar):
                if bar == 0:
                    from engine.strategy import Signal
                    return [Signal.buy("AAPL", weight=1.0)]
                return []

        config = BacktestConfig(initial_capital=100_000)

        # Run first experiment
        r1 = Engine(config).run(BuyHoldStrategy(), data)
        m1 = runner.run_from_engine_result(
            exp_id="exp_001", name="First",
            hypothesis="Test", engine_result=r1,
        )
        assert m1["experiment_id"] == "exp_001"

        # Run second experiment with different config
        config2 = BacktestConfig(initial_capital=200_000)
        r2 = Engine(config2).run(BuyHoldStrategy(), data)
        m2 = runner.run_from_engine_result(
            exp_id="exp_002", name="Second",
            hypothesis="Test 2", engine_result=r2,
        )
        assert m2["experiment_id"] == "exp_002"

        # Both dirs should exist
        assert (Path(td) / "experiments" / "exp_001").exists()
        assert (Path(td) / "experiments" / "exp_002").exists()

        # Verify investment directory has content
        inv_dir = Path(td) / "investments"
        assert any(inv_dir.iterdir()), "Investment records should exist"
