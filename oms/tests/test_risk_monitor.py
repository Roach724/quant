import pytest
import asyncio
from oms.risk_monitor import RiskMonitor
from oms.alerting import AlertManager
from oms.broker import PaperBroker


@pytest.mark.asyncio
async def test_risk_monitor_no_breach():
    broker = PaperBroker(100_000)
    alerts = AlertManager()
    monitor = RiskMonitor(broker, alerts, config={"max_drawdown": 0.20})
    await monitor.check()
    # No trades → no drawdown, no alerts
    assert alerts.count_by_level("critical") == 0


@pytest.mark.asyncio
async def test_risk_monitor_detects_leverage():
    broker = PaperBroker(100_000)
    broker.update_price("AAPL", 150.0)
    await broker.submit_order("AAPL", "buy", 500)  # $75k position on $100k equity = 0.75x leverage
    alerts = AlertManager()
    monitor = RiskMonitor(broker, alerts, config={"max_leverage": 0.5})
    await monitor.check()
    assert alerts.count_by_level("warning") >= 1
