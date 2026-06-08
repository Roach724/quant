"""Integration tests for PaperRunner — full paper-trading replay with simulated data."""

import os
import sys
import tempfile
from datetime import datetime

import pytest

# Ensure project root is importable
_PROJECT_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)


@pytest.fixture
def base_config():
    """Baseline config suitable for a quick paper run."""
    return {
        "market": "us",
        "capital": 100_000,
        "strategy": "BuyHold",
        "strategy_kwargs": {},
        "start": "2024-01-01",
        "end": "2024-01-15",
        "symbols": ["AAPL", "MSFT", "GOOGL"],
        "data_source": "simulated",
        "data_dir": "",
        "output": None,
        "realtime": False,
        "realtime_interval": 0,
    }


class TestPaperRunnerInstantiation:
    def test_create_runner(self, base_config):
        from run_paper import PaperRunner
        runner = PaperRunner(base_config)
        assert runner.market == "us"
        assert runner.broker.cash == 100_000
        assert runner.order_manager is not None
        assert runner.position_tracker is not None

    def test_create_runner_hk(self, base_config):
        from run_paper import PaperRunner
        cfg = {**base_config, "market": "hk", "capital": 1_000_000}
        runner = PaperRunner(cfg)
        assert runner.market == "hk"
        assert runner.broker.cash == 1_000_000

    def test_create_runner_crypto(self, base_config):
        from run_paper import PaperRunner
        cfg = {**base_config, "market": "crypto"}
        runner = PaperRunner(cfg)
        assert runner.market == "crypto"


class TestPaperRunnerLoadData:
    def test_simulated_data(self, base_config):
        from run_paper import PaperRunner
        runner = PaperRunner(base_config)
        ds = runner.load_data(
            "simulated", base_config["symbols"],
            base_config["start"], base_config["end"],
        )
        assert len(ds) > 0
        assert ds.universe == base_config["symbols"]
        assert ds.close.shape[1] == len(base_config["symbols"])

    def test_simulated_data_is_reproducible(self, base_config):
        """Same seed should yield same data."""
        from run_paper import PaperRunner
        runner1 = PaperRunner(base_config)
        runner2 = PaperRunner(base_config)
        ds1 = runner1.load_data(
            "simulated", base_config["symbols"],
            base_config["start"], base_config["end"],
        )
        ds2 = runner2.load_data(
            "simulated", base_config["symbols"],
            base_config["start"], base_config["end"],
        )
        pd = pytest.importorskip("pandas")
        pd.testing.assert_frame_equal(ds1.close, ds2.close)

    def test_invalid_source_raises(self, base_config):
        from run_paper import PaperRunner
        runner = PaperRunner(base_config)
        with pytest.raises(ValueError, match="Unknown data source"):
            runner.load_data(
                "invalid", base_config["symbols"],
                base_config["start"], base_config["end"],
            )


class TestPaperRunnerRun:
    def test_buyhold_full_run(self, base_config):
        """Run BuyHold end-to-end with simulated data."""
        from run_paper import PaperRunner
        runner = PaperRunner(base_config)
        result = runner.run()

        assert "metrics" in result
        assert "output_dir" in result
        assert "n_bars" in result
        assert result["n_bars"] > 0
        assert result["n_trades"] > 0

    def test_simple_momentum_full_run(self, base_config):
        """Run SimpleMomentum end-to-end."""
        from run_paper import PaperRunner
        cfg = {
            **base_config,
            "strategy": "SimpleMomentum",
            "strategy_kwargs": {"lookback": 5, "top_k": 2, "rebalance_every": 3},
            "start": "2024-01-01",
            "end": "2024-06-30",  # longer to accumulate enough bars for lookback
        }
        runner = PaperRunner(cfg)
        result = runner.run()

        assert "metrics" in result
        assert result["metrics"]["total_trades"] >= 0  # may be zero in short run
        assert os.path.isdir(result["output_dir"])

    def test_mean_reversion_full_run(self, base_config):
        """Run MeanReversion end-to-end."""
        from run_paper import PaperRunner
        cfg = {
            **base_config,
            "strategy": "MeanReversion",
            "strategy_kwargs": {"lookback": 5, "top_k": 2},
            "start": "2024-01-01",
            "end": "2024-06-30",
        }
        runner = PaperRunner(cfg)
        result = runner.run()

        assert "metrics" in result
        assert os.path.isdir(result["output_dir"])

    def test_run_produces_output_files(self, base_config):
        """The output directory should contain the expected archive files."""
        from run_paper import PaperRunner
        with tempfile.TemporaryDirectory() as tmpdir:
            cfg = {**base_config, "output": tmpdir}
            runner = PaperRunner(cfg)
            runner.run()

            assert os.path.isfile(os.path.join(tmpdir, "meta.json"))
            assert os.path.isfile(os.path.join(tmpdir, "performance.json"))
            assert os.path.isfile(os.path.join(tmpdir, "trades.csv"))
            assert os.path.isfile(os.path.join(tmpdir, "daily_pnl.csv"))
            assert os.path.isfile(os.path.join(tmpdir, "summary_report.txt"))

    def test_run_with_custom_output_dir(self, base_config):
        """Custom output directory should be created and used."""
        from run_paper import PaperRunner
        with tempfile.TemporaryDirectory() as tmpdir:
            outdir = os.path.join(tmpdir, "custom_run")
            cfg = {**base_config, "output": outdir}
            runner = PaperRunner(cfg)
            result = runner.run()
            assert result["output_dir"] == outdir
            assert os.path.isdir(outdir)

    def test_empty_signals_on_short_data(self, base_config):
        """A strategy with lookback > data length should produce zero signals (no crash)."""
        from run_paper import PaperRunner
        # Very short date range with long lookback → no trading, but no crash
        cfg = {
            **base_config,
            "strategy": "SimpleMomentum",
            "strategy_kwargs": {"lookback": 100, "top_k": 2},
            "start": "2024-01-01",
            "end": "2024-01-10",
        }
        runner = PaperRunner(cfg)
        result = runner.run()
        assert result["n_bars"] > 0

    def test_crypto_run(self, base_config):
        """Crypto market with BuyHold."""
        from run_paper import PaperRunner
        cfg = {
            **base_config,
            "market": "crypto",
            "symbols": ["BTC", "ETH"],
        }
        runner = PaperRunner(cfg)
        result = runner.run()
        assert result["n_bars"] > 0
        # Crypto is always open → skipped_bars should be 0
        assert result["skipped_bars"] == 0


class TestPaperRunnerStrategies:
    def test_buyhold_signals_first_bar_only(self, base_config):
        from run_paper import PaperRunner
        from engine.strategy import StrategyContext
        from strategies import BuyHold

        runner = PaperRunner(base_config)
        ds = runner.load_data(
            "simulated", base_config["symbols"],
            base_config["start"], base_config["end"],
        )

        strategy = BuyHold()
        ctx = StrategyContext(data=ds, portfolio=runner.portfolio, config=base_config)
        strategy.on_init(ctx)

        bar0 = strategy.on_bar(ctx, 0)
        assert len(bar0) == len(base_config["symbols"])

        # Second bar should yield no signals (buy-once)
        bar1 = strategy.on_bar(ctx, 1)
        assert len(bar1) == 0

    def test_momentum_no_signals_before_lookback(self, base_config):
        from run_paper import PaperRunner
        from engine.strategy import StrategyContext
        from strategies import SimpleMomentum

        runner = PaperRunner(base_config)
        ds = runner.load_data(
            "simulated", base_config["symbols"],
            base_config["start"], base_config["end"],
        )

        strategy = SimpleMomentum()
        strategy.lookback = 10
        strategy.top_k = 2
        strategy.rebalance_every = 5
        ctx = StrategyContext(data=ds, portfolio=runner.portfolio, config=base_config)
        strategy.on_init(ctx)

        # Before lookback → no signals
        sigs = strategy.on_bar(ctx, 5)
        assert len(sigs) == 0


class TestPaperRunnerCLI:
    def test_list_strategies(self):
        """--list-strategies should print strategy info."""
        import subprocess
        result = subprocess.run(
            [sys.executable, "run_paper.py", "--list-strategies"],
            capture_output=True, text=True,
            cwd=_PROJECT_ROOT,
        )
        assert result.returncode == 0
        assert "BuyHold" in result.stdout
        assert "SimpleMomentum" in result.stdout
        assert "MeanReversion" in result.stdout

    def test_market_required_without_config(self):
        """Missing --market should error out."""
        import subprocess
        result = subprocess.run(
            [sys.executable, "run_paper.py"],
            capture_output=True, text=True,
            cwd=_PROJECT_ROOT,
        )
        assert result.returncode != 0
        assert "market" in result.stderr.lower() or "market" in result.stdout.lower()

    def test_cli_full_run(self):
        """Full CLI invocation with --market and --strategy."""
        import subprocess
        with tempfile.TemporaryDirectory() as tmpdir:
            result = subprocess.run(
                [
                    sys.executable, "run_paper.py",
                    "--market", "us",
                    "--capital", "50000",
                    "--strategy", "BuyHold",
                    "--start", "2024-01-01",
                    "--end", "2024-01-15",
                    "--symbols", "AAPL", "MSFT",
                    "--data-source", "simulated",
                    "--output", tmpdir,
                ],
                capture_output=True, text=True,
                cwd=_PROJECT_ROOT,
                timeout=30,
            )
            assert result.returncode == 0, f"stderr: {result.stderr}"
            assert os.path.isfile(os.path.join(tmpdir, "meta.json"))
            assert os.path.isfile(os.path.join(tmpdir, "summary_report.txt"))


class TestStrategyResolution:
    def test_builtin_buyhold(self):
        from strategies import get_strategy
        cls = get_strategy("BuyHold")
        assert cls.__name__ == "BuyHold"

    def test_builtin_with_prefix(self):
        from strategies import get_strategy
        cls = get_strategy("strategies.BuyHold")
        assert cls.__name__ == "BuyHold"

    def test_unknown_raises(self):
        from strategies import get_strategy
        with pytest.raises(ValueError, match="Unknown strategy"):
            get_strategy("NonExistent")

    def test_list_strategies_coverage(self):
        from strategies import list_strategies
        result = list_strategies()
        names = {s["name"] for s in result}
        assert "BuyHold" in names
        assert "SimpleMomentum" in names
        assert "MeanReversion" in names
