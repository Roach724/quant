from oms.alerting import Alert, AlertManager, ConsoleHandler

def test_alert_creation():
    a = Alert(level="warning", message="Test alert", context={"symbol": "AAPL"})
    assert a.level == "warning"
    assert a.message == "Test alert"
    assert a.context["symbol"] == "AAPL"
    assert a.timestamp is not None

def test_alert_to_dict():
    a = Alert("info", "msg", {"k": "v"})
    d = a.to_dict()
    assert d["level"] == "info"
    assert d["message"] == "msg"
    assert d["context"] == {"k": "v"}

def test_alert_manager_fire_and_recent():
    mgr = AlertManager()
    mgr.fire("info", "one")
    mgr.fire("warning", "two")
    mgr.fire("critical", "three")
    recent = mgr.recent(2)
    assert len(recent) == 2
    assert recent[0].message == "three"  # most recent first
    assert recent[1].message == "two"

def test_alert_manager_handler():
    mgr = AlertManager()
    received = []
    mgr.on_alert(lambda a: received.append(a))
    mgr.fire("warning", "test")
    assert len(received) == 1
    assert received[0].message == "test"

def test_alert_manager_count():
    mgr = AlertManager()
    mgr.fire("info", "a")
    mgr.fire("warning", "b")
    mgr.fire("warning", "c")
    assert mgr.count_by_level("info") == 1
    assert mgr.count_by_level("warning") == 2
    assert mgr.count_by_level("critical") == 0
