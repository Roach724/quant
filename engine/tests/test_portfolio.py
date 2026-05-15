import pandas as pd
from engine.portfolio import Portfolio, Position
from engine.orders import Order, Fill


def test_position_tracks_pnl():
    p = Position(symbol="AAPL", entry_price=100.0)
    assert p.symbol == "AAPL"
    assert p.size == 0
    assert p.unrealized_pnl(110.0) == 0


def test_position_update():
    p = Position(symbol="AAPL", entry_price=100.0)
    p.add(50, 101.0)
    assert p.size == 50
    assert p.avg_entry == 101.0
    p.add(50, 103.0)
    assert p.size == 100
    assert p.avg_entry == 102.0


def test_portfolio_initial_state():
    pf = Portfolio(initial_capital=200_000)
    assert pf.cash == 200_000
    assert len(pf.positions) == 0
    assert pf.total_equity == 200_000


def test_portfolio_update_from_fill():
    pf = Portfolio(initial_capital=100_000)
    fill = Fill(
        order=Order(symbol="AAPL", side="buy", size=100),
        price=150.0, size=100, slippage=0.15, commission=1.50,
    )
    pf.update([fill], {"close": {"AAPL": 150.0}})
    assert "AAPL" in pf.positions
    assert pf.positions["AAPL"].size == 100
    assert pf.cash == 100_000 - 150.0*100 - 1.50


def test_equity_curve():
    pf = Portfolio(initial_capital=100_000)
    for i in range(5):
        pf.record_snapshot(pd.Timestamp(f"2026-01-0{i+1}"))
    eq = pf.equity_curve
    assert len(eq) == 5
    assert eq.iloc[0] == 100_000
