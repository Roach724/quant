"""Dashboard API — FastAPI server exposing OMS state for the monitoring frontend.

Start: uvicorn dashboard.api:app --port 8090
Endpoints: GET /api/portfolio, /api/positions, /api/orders, /api/alerts, /api/risk
"""

from datetime import datetime, timezone
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(title="Quant Trading Dashboard")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# Global state — populated by the application using the dashboard
_state: dict = {
    "broker": None,
    "order_manager": None,
    "position_tracker": None,
    "alert_manager": None,
    "engine_result": None,
}


def configure(broker=None, order_manager=None, position_tracker=None,
              alert_manager=None, engine_result=None):
    """Wire the dashboard to live OMS objects."""
    global _state
    _state["broker"] = broker
    _state["order_manager"] = order_manager
    _state["position_tracker"] = position_tracker
    _state["alert_manager"] = alert_manager
    _state["engine_result"] = engine_result


async def _get_portfolio(broker):
    if broker is None:
        return {"cash": 0, "equity": 0, "buying_power": 0}
    acc = await broker.get_account()
    return {"cash": acc.cash, "equity": acc.equity, "buying_power": acc.buying_power}


async def _get_positions(broker):
    if broker is None:
        return []
    positions = await broker.get_positions()
    return [{"symbol": p.symbol, "qty": p.qty, "avg_entry": p.avg_entry_price,
             "market_value": p.market_value, "unrealized_pnl": p.unrealized_pnl} for p in positions]


def _get_orders(order_manager):
    if order_manager is None:
        return {"open": [], "history": []}
    open_orders = order_manager.get_open_orders()
    history = order_manager.get_order_history()
    return {
        "open": [{"id": o.internal_id, "broker_id": o.broker_id, "symbol": o.symbol,
                  "side": o.side, "qty": o.qty, "filled_qty": o.filled_qty, "state": o.state,
                  "avg_price": o.avg_fill_price} for o in open_orders],
        "history": [{"id": o.internal_id, "symbol": o.symbol, "side": o.side,
                     "qty": o.qty, "filled_qty": o.filled_qty, "state": o.state,
                     "strategy": o.strategy_name} for o in history[-50:]],
    }


def _get_alerts(alert_manager):
    if alert_manager is None:
        return []
    return [a.to_dict() for a in alert_manager.recent(50)]


def _get_risk_metrics(broker, alert_manager):
    metrics = {"drawdown_pct": 0, "leverage": 0, "concentration_max": 0,
               "cash_ratio": 0, "alerts_info": 0, "alerts_warning": 0, "alerts_critical": 0}
    if alert_manager:
        metrics["alerts_info"] = alert_manager.count_by_level("info")
        metrics["alerts_warning"] = alert_manager.count_by_level("warning")
        metrics["alerts_critical"] = alert_manager.count_by_level("critical")
    return metrics


@app.get("/api/portfolio")
async def portfolio():
    return await _get_portfolio(_state.get("broker"))


@app.get("/api/positions")
async def positions():
    return await _get_positions(_state.get("broker"))


@app.get("/api/orders")
async def orders():
    return _get_orders(_state.get("order_manager"))


@app.get("/api/alerts")
async def alerts():
    return _get_alerts(_state.get("alert_manager"))


@app.get("/api/risk")
async def risk():
    return _get_risk_metrics(_state.get("broker"), _state.get("alert_manager"))


@app.get("/api/status")
async def status():
    return {
        "broker_connected": _state.get("broker") is not None,
        "order_manager_ready": _state.get("order_manager") is not None,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
