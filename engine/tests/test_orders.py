from engine.orders import Order, Fill, simulate_fills


def test_order_creation():
    o = Order(symbol="AAPL", side="buy", size=100)
    assert o.symbol == "AAPL"
    assert o.size == 100
    assert o.order_type == "market"
    assert o.limit_price is None


def test_fill_simulation_buy():
    from engine.config import BacktestConfig

    cfg = BacktestConfig(slippage_bps=10, commission_bps=2, min_commission=0.5)
    orders = [Order(symbol="AAPL", side="buy", size=100)]
    bar_data = {"close": {"AAPL": 150.0}}

    fills = simulate_fills(orders, bar_data, cfg)
    assert len(fills) == 1
    f = fills[0]
    assert f.price == 150.0 + (10 / 10000 * 150.0)
    assert f.size == 100
    assert f.slippage > 0
    assert f.commission > 0


def test_fill_simulation_sell():
    from engine.config import BacktestConfig

    cfg = BacktestConfig(slippage_bps=0)
    orders = [Order(symbol="AAPL", side="sell", size=50)]
    bar_data = {"close": {"AAPL": 200.0}}
    fills = simulate_fills(orders, bar_data, cfg)
    assert fills[0].price == 200.0
