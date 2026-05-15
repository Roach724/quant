import pytest
from oms.broker import PaperBroker
from oms.position import PositionTracker


def test_record_fill():
    tracker = PositionTracker(broker=None)
    tracker.record_fill("AAPL", "buy", 10)
    assert tracker.positions == {"AAPL": 10}
    tracker.record_fill("AAPL", "sell", 3)
    assert tracker.positions == {"AAPL": 7}
    tracker.record_fill("MSFT", "buy", 5)
    assert tracker.positions == {"AAPL": 7, "MSFT": 5}


@pytest.mark.asyncio
async def test_reconcile_no_issues():
    broker = PaperBroker(initial_capital=100_000.0)
    await broker.submit_order("AAPL", "buy", 10)
    tracker = PositionTracker(broker)
    tracker.record_fill("AAPL", "buy", 10)
    issues = await tracker.reconcile()
    assert len(issues) == 0


@pytest.mark.asyncio
async def test_reconcile_detects_mismatch():
    broker = PaperBroker(initial_capital=100_000.0)
    await broker.submit_order("AAPL", "buy", 10)
    tracker = PositionTracker(broker)
    tracker.record_fill("AAPL", "buy", 15)
    issues = await tracker.reconcile()
    assert len(issues) == 1
    assert "local=15" in issues[0]
    assert "broker=10" in issues[0]


@pytest.mark.asyncio
async def test_reconcile_detects_missing_local():
    broker = PaperBroker(initial_capital=100_000.0)
    await broker.submit_order("AAPL", "buy", 10)
    tracker = PositionTracker(broker)
    issues = await tracker.reconcile()
    assert len(issues) == 1
    assert "missing local" in issues[0]
