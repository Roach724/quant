import pytest
from oms.state import TrackedOrder, VALID_STATES


def test_tracked_order_initial_state():
    t = TrackedOrder(symbol="AAPL", side="buy", qty=10)
    assert t.state == "PENDING"
    assert t.internal_id is not None
    assert len(t.internal_id) == 8
    assert t.symbol == "AAPL"
    assert t.side == "buy"
    assert t.qty == 10
    assert t.filled_qty == 0


def test_tracked_order_transitions():
    t = TrackedOrder()
    t.transition("SUBMITTED")
    assert t.state == "SUBMITTED"
    t.transition("ACKNOWLEDGED")
    assert t.state == "ACKNOWLEDGED"
    t.transition("PARTIAL_FILL")
    assert t.state == "PARTIAL_FILL"
    t.transition("FILLED")
    assert t.state == "FILLED"


def test_tracked_order_rejected():
    t = TrackedOrder(symbol="AAPL")
    t.transition("REJECTED")
    assert t.state == "REJECTED"


def test_tracked_order_invalid_transition():
    t = TrackedOrder()
    with pytest.raises(ValueError, match="Invalid state"):
        t.transition("INVALID_STATE")
