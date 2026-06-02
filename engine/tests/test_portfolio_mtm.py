# engine/tests/test_portfolio_mtm.py
import pytest
from engine.portfolio import Portfolio, Position


def test_mark_to_market_uses_last_price_when_symbol_missing():
    """bar 缺少 symbol 时，估值用最近可用价格而非 0"""
    pf = Portfolio(initial_capital=100000)
    pf.cash = 85000.0  # bought 100 × $150
    pf.positions["APPL"] = Position(symbol="APPL")
    pf.positions["APPL"].add(100, 150.0)

    # 第一根 bar 有 APPL，第二根没有
    bar1 = {"close": {"APPL": 155.0}}
    pf.mark_and_record("2024-01-01 09:30", bar1)

    bar2 = {"close": {}}
    eq = pf._mark_to_market(bar2)

    # eq = cash(85000) + 100*155(last_price) = 100500
    assert eq == pytest.approx(100500.0, rel=1e-6)


def test_mark_to_market_uses_actual_price_when_available():
    """bar 有数据时用实际价格"""
    pf = Portfolio(initial_capital=100000)
    pf.cash = 85000.0  # bought 100 × $150
    pf.positions["APPL"] = Position(symbol="APPL")
    pf.positions["APPL"].add(100, 150.0)

    bar1 = {"close": {"APPL": 155.0}}
    pf.mark_and_record("2024-01-01 09:30", bar1)

    bar2 = {"close": {"APPL": 160.0}}
    eq = pf._mark_to_market(bar2)
    # eq = cash(85000) + 100*160 = 101000
    assert eq == pytest.approx(101000.0, rel=1e-6)


def test_last_price_initial_zero_for_unknown_symbol():
    """从未见过 bar 的 symbol，初始价格=0"""
    pf = Portfolio(initial_capital=100000)
    pf.cash = 95000.0  # bought 100 × $50
    pf.positions["NEW"] = Position(symbol="NEW")
    pf.positions["NEW"].add(100, 50.0)

    bar = {"close": {}}
    eq = pf._mark_to_market(bar)
    # cash=95000 + 100*0.0 (no last_price for NEW) = 95000
    assert eq == pytest.approx(95000.0, rel=1e-6)


def test_last_prices_persisted_in_serialisation():
    """_last_prices 应该被序列化和恢复"""
    from live.state import StateManager

    pf = Portfolio(initial_capital=100000)
    pf._last_prices = {"APPL": 155.0, "MSFT": 420.0}

    serialised = StateManager._serialise_portfolio(pf)
    assert "last_prices" in serialised
    assert serialised["last_prices"] == {"APPL": 155.0, "MSFT": 420.0}

    restored = StateManager.restore_portfolio(serialised, Portfolio, Position)
    assert restored._last_prices == {"APPL": 155.0, "MSFT": 420.0}
