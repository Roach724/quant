"""Tests for InvestmentRecord."""
import os
import tempfile
from datetime import datetime
from pathlib import Path

from experiment.investment_record import InvestmentRecord


def test_record_trade():
    rec = InvestmentRecord("test_strategy", {"capital": 100000})
    rec.record_trade(datetime.now(), "AAPL", "BUY", 100, 150.0, 5.0)
    assert len(rec._trades) == 1
    t = rec._trades[0]
    assert t["symbol"] == "AAPL"
    assert t["side"] == "BUY"
    assert t["qty"] == 100
    assert t["price"] == 150.0
    assert t["cost"] == 5.0


def test_record_equity_chain():
    rec = InvestmentRecord("test")
    rec.record_equity("2024-01-01", 100000)
    rec.record_equity("2024-01-02", 101000)
    rec.record_equity("2024-01-03", 102000)
    perf = rec._compute_performance()
    assert perf["total_return"] > 0
    # 2% return over 3 days
    assert abs(perf["total_return"] - 0.02) < 1e-10
    assert perf["total_trades"] == 0


def test_save_creates_archive():
    rec = InvestmentRecord("paper_trading", {"capital": 1000000})
    rec.record_trade(datetime(2024, 1, 2), "0700.HK", "BUY", 100, 300.0, 15.0)
    rec.record_trade(datetime(2024, 1, 5), "0700.HK", "SELL", 100, 310.0, 15.0)
    rec.record_equity("2024-01-01", 1000000)
    rec.record_equity("2024-01-02", 1000300)
    rec.record_equity("2024-01-03", 1000500)
    rec.record_equity("2024-01-04", 1000800)
    rec.record_equity("2024-01-05", 1011000)
    rec.record_signal("2024-01-01", "0700.HK", 0.85, 1)

    with tempfile.TemporaryDirectory() as td:
        rec.save(td)
        assert os.path.exists(os.path.join(td, "meta.json"))
        assert os.path.exists(os.path.join(td, "trades.csv"))
        assert os.path.exists(os.path.join(td, "daily_pnl.csv"))
        assert os.path.exists(os.path.join(td, "summary_report.txt"))

        # Also check signal_log.csv exists since we recorded signals
        assert os.path.exists(os.path.join(td, "signal_log.csv"))

        # Verify meta.json content
        with open(os.path.join(td, "meta.json")) as f:
            import json
            meta = json.load(f)
            assert meta["strategy_name"] == "paper_trading"
            assert meta["config"]["capital"] == 1000000
            assert meta["n_trades"] == 2
            assert meta["n_signals"] == 1


def test_generate_summary():
    rec = InvestmentRecord("backtest")
    rec.record_trade(datetime(2024, 1, 2), "AAPL", "BUY", 100, 150.0, 5.0)
    rec.record_equity("2024-01-01", 100000)
    rec.record_equity("2024-01-02", 101000)
    summary = rec.generate_summary()
    assert "backtest" in summary
    assert "trades" in summary.lower() or "Trade" in summary


def test_empty_record():
    rec = InvestmentRecord("empty")
    with tempfile.TemporaryDirectory() as td:
        rec.save(td)
        assert os.path.exists(os.path.join(td, "meta.json"))
        # Should not crash even with no data
        assert os.path.exists(os.path.join(td, "trades.csv"))
        assert os.path.exists(os.path.join(td, "daily_pnl.csv"))
        assert os.path.exists(os.path.join(td, "positions_final.csv"))


def test_single_data_point():
    """Edge case: a single equity point should not crash."""
    rec = InvestmentRecord("single")
    rec.record_equity("2024-01-01", 100000)
    perf = rec._compute_performance()
    assert perf["total_return"] == 0.0
    assert perf["sharpe_ratio"] == 0.0


def test_all_zero_performance():
    """Edge case: all-zero equity curve."""
    rec = InvestmentRecord("zeros")
    rec.record_equity("2024-01-01", 0)
    rec.record_equity("2024-01-02", 0)
    perf = rec._compute_performance()
    assert perf["total_return"] == 0.0


def test_risk_events():
    """Risk events are recorded and saved."""
    rec = InvestmentRecord("risk_test")
    rec.record_risk_event("drawdown_halt", "Max drawdown exceeded 20%")
    rec.record_risk_event("position_limit", "Position limit reached for AAPL")

    with tempfile.TemporaryDirectory() as td:
        rec.save(td)
        assert os.path.exists(os.path.join(td, "risk_events.csv"))

    summary = rec.generate_summary()
    assert "Risk Events" in summary
    assert "drawdown_halt" in summary


def test_record_position():
    """Positions are recorded and saved."""
    rec = InvestmentRecord("pos_test")
    rec.record_position("2024-01-01", "AAPL", 100, 150.0)
    rec.record_position("2024-01-01", "MSFT", 50, 300.0)
    assert len(rec._positions) == 2

    with tempfile.TemporaryDirectory() as td:
        rec.save(td)
        assert os.path.exists(os.path.join(td, "positions_final.csv"))


def test_performance_metrics():
    """Verify computed performance metrics are reasonable."""
    rec = InvestmentRecord("perf_test")
    rec.record_equity("2024-01-01", 100000)
    rec.record_equity("2024-01-02", 100500)
    rec.record_equity("2024-01-03", 100300)
    rec.record_equity("2024-01-04", 101000)
    rec.record_equity("2024-01-05", 102000)

    perf = rec._compute_performance()
    assert perf["total_return"] > 0
    assert perf["win_rate"] > 0
    assert perf["max_drawdown"] <= 0  # drawdown is always <= 0
    assert perf["total_trades"] == 0
