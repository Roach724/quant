"""Integration tests: Engine Signal → OMS Bridge → PaperBroker → Fill → Reconcile."""
import pytest, asyncio
import pandas as pd
from engine.strategy import Strategy, Signal, StrategyContext
from engine.data import DataFrameSource
from engine.config import BacktestConfig
from engine.engine import Engine
from engine.portfolio import Portfolio
from oms.bridge import convert_signal, forward_signal, MarketDataBridge, reconcile
from oms.broker import PaperBroker
from oms.manager import OrderManager
from oms.position import PositionTracker
from oms.broker.market_data import MarketDataStream
from execution.twap import TWAPExecutor


def test_convert_signal_buy():
    pf = Portfolio(100_000)
    sig = Signal.buy("AAPL", weight=0.5)
    d = convert_signal(sig, pf)
    assert d["symbol"] == "AAPL"
    assert d["side"] == "buy"
    assert d["qty"] > 0
    assert d["signal_id"] == sig.signal_id


def test_convert_signal_close_maps_to_sell():
    pf = Portfolio(100_000)
    sig = Signal.close("AAPL")
    d = convert_signal(sig, pf)
    assert d["side"] == "sell"


def test_convert_signal_respects_explicit_qty():
    pf = Portfolio(100_000)
    sig = Signal.buy("MSFT", weight=0.5)
    sig.qty = 42
    d = convert_signal(sig, pf)
    assert d["qty"] == 42


def test_forward_signal_via_paper_broker():
    broker = PaperBroker(100_000)
    broker.update_price("AAPL", 150.0)
    mgr = OrderManager(broker)
    pt = PositionTracker(broker)
    signal_dict = {"symbol": "AAPL", "side": "buy", "qty": 100,
                   "order_type": "market", "signal_id": "test-1"}

    results = forward_signal(signal_dict, broker, mgr, position_tracker=pt)
    assert len(results) >= 1
    t = results[0]
    assert t.symbol == "AAPL"
    assert t.state in ("filled", "FILLED")
    assert t.filled_qty > 0
    assert pt.positions.get("AAPL", 0) == 100


def test_forward_signal_with_twap():
    broker = PaperBroker(100_000)
    broker.update_price("MSFT", 300.0)
    mgr = OrderManager(broker)
    pt = PositionTracker(broker)
    twap = TWAPExecutor(window_seconds=1, slices=2, randomize=False)
    signal_dict = {"symbol": "MSFT", "side": "sell", "qty": 60}

    results = forward_signal(signal_dict, broker, mgr, execution_algo=twap, position_tracker=pt)
    assert len(results) == 2  # 2 slices
    assert pt.positions.get("MSFT", 0) == -60


def test_limit_order_fills_when_price_crossed():
    broker = PaperBroker(100_000)
    broker.update_price("AAPL", 200.0)  # price above limit
    mgr = OrderManager(broker)
    signal_dict = {"symbol": "AAPL", "side": "buy", "qty": 50,
                   "order_type": "limit", "limit_price": 190.0}
    # Price 200 > limit 190, buy limit NOT crossed (wait for price to drop)
    results = forward_signal(signal_dict, broker, mgr)
    t = results[0]
    assert t.state == "PENDING"  # OMS reflects state at submission

    # Price drops below limit — broker fills it internally
    broker.update_price("AAPL", 185.0)
    # Broker order should now be filled
    broker_order = broker._orders.get(t.broker_id)
    assert broker_order is not None
    assert broker_order.status == "filled"

    # Check position updated
    positions = asyncio.run(broker.get_positions())
    assert any(p.symbol == "AAPL" and p.qty == 50 for p in positions)


def test_reconcile_detects_mismatch():
    pf = Portfolio(100_000)
    pf.positions["AAPL"] = type("Pos", (), {"size": 100})()
    broker = PaperBroker(100_000)
    pt = PositionTracker(broker)
    pt.record_fill("AAPL", "buy", 50)
    issues = reconcile(pf, pt)
    assert len(issues) > 0


def test_market_data_bridge():
    dates = pd.date_range("2026-01-01", periods=5, freq="D")
    close = pd.DataFrame({"AAPL": [100, 101, 102, 103, 104]}, index=dates)
    data = DataFrameSource(close=close)
    stream = MarketDataStream()
    received = []
    stream.on_bar(lambda b: received.append(b))
    bridge = MarketDataBridge(stream, data)
    asyncio.run(bridge.feed_bars(0, 3))
    assert len(received) == 3
    bar = asyncio.run(bridge.latest_bar("AAPL"))
    assert bar["close"] == 102
