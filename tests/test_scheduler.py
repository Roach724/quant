"""Tests for RebalanceScheduler."""
import json
import tempfile
from pathlib import Path

import pytest

from trading.scheduler import RebalanceScheduler, Decision


def _make_sched(lookback=10, every=5, freq=5, path=None):
    if path is None:
        path = str(Path(tempfile.mkdtemp()) / "test_scheduler.json")
    return RebalanceScheduler(
        freq_minutes=freq,
        lookback_bars=lookback,
        rebalance_every=every,
        state_path=path,
    )


class TestLookbackPhase:

    def test_returns_waiting_during_lookback(self):
        sched = _make_sched(lookback=10, every=5)
        for _ in range(9):
            assert sched.on_bar() == Decision.WAITING

    def test_returns_trade_on_first_bar_after_lookback(self):
        sched = _make_sched(lookback=10, every=5)
        for _ in range(9):
            sched.on_bar()
        # 10th call -> bar 10, equals lookback -> first TRADE
        assert sched.on_bar() == Decision.TRADE


class TestNormalInterval:

    def test_skips_within_rebalance_window(self):
        sched = _make_sched(lookback=10, every=5)
        for _ in range(10):
            sched.on_bar()
        # bar=10 -> first TRADE done, last_rebalance=10
        for _ in range(4):
            assert sched.on_bar() == Decision.SKIP
        # bar=15 -> TRADE
        assert sched.on_bar() == Decision.TRADE

    def test_trade_at_every_n_bars(self):
        sched = _make_sched(lookback=10, every=5)
        decisions = []
        for _ in range(25):
            decisions.append(sched.on_bar())
        # bar 1-9: 9 WAITING, bar 10: TRADE,
        # bar 11-14: 4 SKIP, bar 15: TRADE,
        # bar 16-19: 4 SKIP, bar 20: TRADE,
        # bar 21-24: 4 SKIP, bar 25: TRADE
        expected = (
            [Decision.WAITING] * 9
            + [Decision.TRADE]
            + [Decision.SKIP] * 4
            + [Decision.TRADE]
            + [Decision.SKIP] * 4
            + [Decision.TRADE]
            + [Decision.SKIP] * 4
            + [Decision.TRADE]
        )
        assert decisions == expected


class TestPersistence:

    def test_save_and_write(self):
        path = str(Path(tempfile.mkdtemp()) / "sched.json")
        sched = _make_sched(path=path)
        for _ in range(15):
            sched.on_bar()
        sched.write()

        saved = json.loads(Path(path).read_text())
        assert saved["bar_count"] == 15

    def test_from_file_fresh(self):
        path = str(Path(tempfile.mkdtemp()) / "fresh.json")
        sched = RebalanceScheduler.from_file(
            file_path=path, freq_minutes=5, lookback_bars=10, rebalance_every=5,
        )
        assert sched.bar_count == 0
        assert sched.last_rebalance_bar is None

    def test_from_file_restore(self):
        path = str(Path(tempfile.mkdtemp()) / "restore.json")
        sched1 = _make_sched(path=path)
        for _ in range(12):
            sched1.on_bar()
        sched1.write()

        sched2 = RebalanceScheduler.from_file(
            file_path=path, freq_minutes=5, lookback_bars=10, rebalance_every=5,
        )
        assert sched2.bar_count == 12


class TestRestartScenarios:

    def test_restart_short_gap_skips(self):
        sched = _make_sched()
        sched.load_state(bar_count=107, last_rebalance_bar=105)
        assert sched.on_bar() == Decision.SKIP
        assert sched.on_bar() == Decision.SKIP
        assert sched.on_bar() == Decision.TRADE
        assert sched.last_rebalance_bar == 110

    def test_restart_long_gap_trades_once(self):
        sched = _make_sched()
        sched.load_state(bar_count=112, last_rebalance_bar=105)
        assert sched.on_bar() == Decision.TRADE
        assert sched.last_rebalance_bar == 113
        for _ in range(4):
            assert sched.on_bar() == Decision.SKIP
        assert sched.on_bar() == Decision.TRADE

    def test_restart_exact_window_equals(self):
        sched = _make_sched()
        sched.load_state(bar_count=110, last_rebalance_bar=105)
        assert sched.on_bar() == Decision.TRADE
        assert sched.last_rebalance_bar == 111

    def test_load_state_before_lookback(self):
        sched = _make_sched(lookback=10, every=5)
        sched.load_state(bar_count=5, last_rebalance_bar=None)
        for _ in range(4):
            assert sched.on_bar() == Decision.WAITING
        assert sched.on_bar() == Decision.TRADE


class TestValidation:

    def test_negative_lookback_raises(self):
        with pytest.raises(ValueError):
            _make_sched(lookback=-1)

    def test_zero_rebalance_every_raises(self):
        with pytest.raises(ValueError):
            _make_sched(every=0)

    def test_logs_warning_for_short_interval(self, caplog):
        sched = _make_sched(freq=5, every=2)
        assert sched.rebalance_interval_minutes == 10
        assert any("only" in rec.message and "minutes" in rec.message for rec in caplog.records)
