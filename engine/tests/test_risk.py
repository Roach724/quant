from engine.risk import RiskEngine
from engine.risk.stop_loss import StopLoss
from engine.risk.volatility_target import VolatilityTarget
from engine.risk.exposure import ExposureLimit
from engine.portfolio import Portfolio
from engine.orders import Order


def test_stop_loss_closes_position():
    pf = Portfolio(100_000)
    class FakePos:
        symbol = "AAPL"
        size = 100
        avg_entry = 100.0
        def unrealized_pnl(self, p):
            return self.size * (p - self.avg_entry)
    pf.positions["AAPL"] = FakePos()
    rule = StopLoss(pct=0.05)
    orders = [Order(symbol="AAPL", side="buy", size=10)]
    bar_data = {"close": {"AAPL": 94.0}}
    result = rule.apply(orders, pf, bar_data)
    assert any(o.side == "sell" and o.symbol == "AAPL" for o in result)


def test_stop_loss_passes_when_not_triggered():
    pf = Portfolio(100_000)
    class FakePos:
        symbol = "AAPL"
        size = 100
        avg_entry = 100.0
        def unrealized_pnl(self, p):
            return self.size * (p - self.avg_entry)
    pf.positions["AAPL"] = FakePos()
    rule = StopLoss(pct=0.05)
    orders = [Order(symbol="AAPL", side="buy", size=10)]
    bar_data = {"close": {"AAPL": 97.0}}
    result = rule.apply(orders, pf, bar_data)
    assert len(result) == 1


def test_volatility_target_scales_size():
    """VolatilityTarget is not yet implemented — verifies it raises cleanly."""
    import pytest
    pf = Portfolio(100_000)
    rule = VolatilityTarget(annual=0.20)
    orders = [Order(symbol="AAPL", side="buy", size=1000)]
    bar_data = {"close": {"AAPL": 150.0}}
    with pytest.raises(NotImplementedError, match="VolatilityTarget"):
        rule.apply(orders, pf, bar_data)


def test_risk_engine_composes_rules():
    class RejectAll:
        def apply(self, orders, pf, bar_data):
            return []
    class PassThrough:
        def apply(self, orders, pf, bar_data):
            return orders
    engine = RiskEngine(rules=[PassThrough(), RejectAll()])
    orders = [Order(symbol="AAPL", side="buy", size=10)]
    result = engine.check(orders, Portfolio(100_000), {})
    assert result == []


def test_exposure_limit():
    pf = Portfolio(100_000)
    class FakePos:
        symbol = "AAPL"
        size = 500
    pf.positions["AAPL"] = FakePos()
    rule = ExposureLimit(max_pct=0.2)
    orders = [Order(symbol="AAPL", side="buy", size=100)]
    bar_data = {"close": {"AAPL": 150.0}}
    result = rule.apply(orders, pf, bar_data)
    assert len(result) == 0
