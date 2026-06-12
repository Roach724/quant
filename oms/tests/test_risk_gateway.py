import pytest
import asyncio
from oms.risk_gateway import RiskGateway
from oms.alerting import AlertManager
from engine.risk.exposure import ExposureLimit
from oms.broker import PaperBroker
from engine.orders import Order
from engine.portfolio import Portfolio


@pytest.mark.asyncio
async def test_risk_gateway_approves_safe_order():
    broker = PaperBroker(100_000)
    alerts = AlertManager()
    pf = Portfolio(100_000)
    gateway = RiskGateway([ExposureLimit(max_pct=0.5)], broker, alerts)
    orders = [Order(symbol="AAPL", side="buy", size=10)]
    approved, rejected = await gateway.check(orders, pf, {"close": {"AAPL": 150.0}})
    assert len(approved) == 1
    assert len(rejected) == 0


@pytest.mark.asyncio
async def test_risk_gateway_rejects_oversized_order():
    broker = PaperBroker(100_000)
    alerts = AlertManager()
    pf = Portfolio(100_000)
    gateway = RiskGateway([ExposureLimit(max_pct=0.01)], broker, alerts)
    orders = [Order(symbol="AAPL", side="buy", size=100)]
    approved, rejected = await gateway.check(orders, pf, {"close": {"AAPL": 150.0}})
    assert len(rejected) == 1
    assert alerts.count_by_level("warning") >= 1


@pytest.mark.asyncio
async def test_risk_gateway_no_rules_passes_all():
    broker = PaperBroker(100_000)
    pf = Portfolio(100_000)
    gateway = RiskGateway([], broker)
    orders = [Order(symbol="AAPL", side="buy", size=1000)]
    approved, rejected = await gateway.check(orders, pf, {"close": {"AAPL": 150.0}})
    assert len(approved) == 1
    assert len(rejected) == 0
