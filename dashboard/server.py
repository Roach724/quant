"""Dashboard Server — FastAPI backend querying BigQuery for experiment & pipeline data.

Start: uvicorn dashboard.server:app --port 8090
Endpoints:
  GET  /                          serve dashboard/index.html
  GET  /api/experiments           latest equity snapshot per experiment
  GET  /api/equity/{exp_id}       equity time-series for one experiment
  GET  /api/trades/{exp_id}       recent trades for one experiment
  GET  /api/pipeline              data-freshness check (us_bars_5m, hk_bars_5m)
  WS   /ws/live                   WebSocket broadcast channel
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse

from google.cloud import bigquery

logger = logging.getLogger(__name__)

PROJECT = "deductive-notch-495015-c2"
DATASET = "quant"

# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------
app = FastAPI(title="Quant Trading Dashboard — Experiments")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# BigQuery client (lazy singleton)
# ---------------------------------------------------------------------------
_bq_client: bigquery.Client | None = None


def _get_bq() -> bigquery.Client:
    global _bq_client
    if _bq_client is None:
        _bq_client = bigquery.Client(project=PROJECT)
    return _bq_client


def _table(table_name: str) -> str:
    return f"{PROJECT}.{DATASET}.{table_name}"


# ---------------------------------------------------------------------------
# JSON serialization helpers
# ---------------------------------------------------------------------------
def _serialize(obj: Any) -> Any:
    """Convert BQ types (Timestamp, etc.) to JSON-safe Python types."""
    if isinstance(obj, datetime):
        return obj.isoformat()
    if hasattr(obj, "isoformat"):
        return obj.isoformat()
    return obj


def _row_to_dict(row: Any, names: list[str]) -> dict[str, Any]:
    """Convert a BigQuery Row to a plain dict with serialized values."""
    return {k: _serialize(v) for k, v in zip(names, row.values())}


# ---------------------------------------------------------------------------
# GET / — serve the dashboard HTML
# ---------------------------------------------------------------------------
_HTML_PATH = Path(__file__).resolve().parent / "index.html"


@app.get("/", response_class=HTMLResponse)
async def root():
    if _HTML_PATH.exists():
        return HTMLResponse(_HTML_PATH.read_text(encoding="utf-8"))
    return HTMLResponse("<h1>Dashboard</h1><p>index.html not found</p>", status_code=404)


# ---------------------------------------------------------------------------
# GET /api/experiments — latest equity snapshot per experiment
# ---------------------------------------------------------------------------
@app.get("/api/experiments")
async def experiments():
    """Return the most-recent equity snapshot for every distinct experiment."""
    client = _get_bq()
    query = f"""
        SELECT * EXCEPT (rn)
        FROM (
            SELECT *,
                   ROW_NUMBER() OVER (PARTITION BY exp_id ORDER BY ts DESC) AS rn
            FROM {_table("experiment_equity")}
            WHERE NOT STARTS_WITH(exp_id, "test_")
        )
        WHERE rn = 1
        ORDER BY ts DESC
    """
    try:
        rows = client.query(query).result()
        columns = [f.name for f in rows.schema]
        return [{"exp_id": row.exp_id, "ts": _serialize(row.ts),
                 "bar": row.bar, "equity": row.equity, "cash": row.cash,
                 "portfolio_value": row.portfolio_value, "daily_pnl": row.daily_pnl,
                 "drawdown": row.drawdown}
                for row in rows]
    except Exception as exc:
        logger.error("experiments query error: %s", exc)
        return []


# ---------------------------------------------------------------------------
# GET /api/equity/{exp_id} — time-series equity for one experiment
# ---------------------------------------------------------------------------
@app.get("/api/equity/{exp_id}")
async def equity_series(exp_id: str):
    """Return the full equity curve for a single experiment (ordered by bar)."""
    client = _get_bq()
    query = f"""
        SELECT ts, bar, equity, cash, portfolio_value, daily_pnl, drawdown
        FROM {_table("experiment_equity")}
        WHERE exp_id = @exp_id
        ORDER BY bar ASC
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[bigquery.ScalarQueryParameter("exp_id", "STRING", exp_id)]
    )
    try:
        rows = client.query(query, job_config=job_config).result()
        return [_row_to_dict(r, ["ts", "bar", "equity", "cash",
                                  "portfolio_value", "daily_pnl", "drawdown"])
                for r in rows]
    except Exception as exc:
        logger.error("equity_series query error for %s: %s", exp_id, exc)
        return []


# ---------------------------------------------------------------------------
# GET /api/trades/{exp_id} — recent trades for one experiment
# ---------------------------------------------------------------------------
@app.get("/api/trades/{exp_id}")
async def trades(exp_id: str, limit: int = 200):
    """Return the most-recent trades for an experiment."""
    client = _get_bq()
    query = f"""
        SELECT ts, bar, symbol, side, qty, price, commission
        FROM {_table("experiment_trades")}
        WHERE exp_id = @exp_id
        ORDER BY ts DESC
        LIMIT @limit
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("exp_id", "STRING", exp_id),
            bigquery.ScalarQueryParameter("limit", "INT64", limit),
        ]
    )
    try:
        rows = client.query(query, job_config=job_config).result()
        return [_row_to_dict(r, ["ts", "bar", "symbol", "side",
                                  "qty", "price", "commission"])
                for r in rows]
    except Exception as exc:
        logger.error("trades query error for %s: %s", exp_id, exc)
        return []


# ---------------------------------------------------------------------------
# GET /api/pipeline — data-freshness check
# ---------------------------------------------------------------------------
@app.get("/api/pipeline")
async def pipeline():
    """Return the most-recent timestamp per market from the bars tables."""
    client = _get_bq()
    result: dict[str, Any] = {
        "us": None, "hk": None,
        "us_open": False, "hk_open": False,
        "ts": datetime.now(timezone.utc).isoformat(),
    }

    # Market hours check (UTC)
    now = datetime.now(timezone.utc)
    # US: 13:30-20:00 UTC Mon-Fri
    result["us_open"] = (
        now.weekday() < 5 and
        datetime(now.year, now.month, now.day, 13, 30, tzinfo=timezone.utc) <= now <=
        datetime(now.year, now.month, now.day, 20, 0, tzinfo=timezone.utc)
    )
    # HK: 01:30-08:00 UTC Mon-Fri
    result["hk_open"] = (
        now.weekday() < 5 and
        datetime(now.year, now.month, now.day, 1, 30, tzinfo=timezone.utc) <= now <=
        datetime(now.year, now.month, now.day, 8, 0, tzinfo=timezone.utc)
    )

    try:
        q = f"""
            SELECT MAX(timestamp) AS latest FROM {_table("us_bars_5m")}
            WHERE timestamp >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 24 HOUR)
        """
        rows = list(client.query(q).result())
        if rows and rows[0].latest:
            result["us"] = _serialize(rows[0].latest)
    except Exception as exc:
        logger.error("pipeline us query error: %s", exc)

    try:
        q = f"""
            SELECT MAX(timestamp) AS latest FROM {_table("hk_bars_5m")}
            WHERE timestamp >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 24 HOUR)
        """
        rows = list(client.query(q).result())
        if rows and rows[0].latest:
            result["hk"] = _serialize(rows[0].latest)
    except Exception as exc:
        logger.error("pipeline hk query error: %s", exc)

    return result


# ---------------------------------------------------------------------------
# WebSocket /ws/live — broadcast channel for live updates
# ---------------------------------------------------------------------------
import asyncio  # noqa: E402


class ConnectionManager:
    """Manages active WebSocket connections for broadcasting."""

    def __init__(self) -> None:
        self._connections: list[WebSocket] = []
        self._lock = asyncio.Lock()

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        async with self._lock:
            self._connections.append(ws)
        logger.info("WebSocket connected (%d total)", len(self._connections))

    async def disconnect(self, ws: WebSocket) -> None:
        async with self._lock:
            if ws in self._connections:
                self._connections.remove(ws)
        logger.info("WebSocket disconnected (%d remaining)", len(self._connections))

    async def broadcast(self, data: dict[str, Any]) -> None:
        """Send JSON payload to all connected clients. Non-blocking."""
        payload = json.dumps(data, default=_serialize)
        async with self._lock:
            connections = list(self._connections)  # snapshot; safe to iterate
        # Fire-and-forget sends — don't block on slow clients
        for ws in connections:
            try:
                await ws.send_text(payload)
            except Exception:
                logger.debug("broadcast send failed for one connection", exc_info=True)


manager = ConnectionManager()


@app.websocket("/ws/live")
async def ws_live(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        # Keep the connection alive and echo any received message back.
        # The client can also subscribe to periodic broadcasts via the manager.
        while True:
            data = await websocket.receive_text()
            # Echo with metadata
            await websocket.send_text(json.dumps({
                "type": "echo",
                "received": data,
                "ts": datetime.now(timezone.utc).isoformat(),
            }))
    except WebSocketDisconnect:
        await manager.disconnect(websocket)
    except Exception:
        await manager.disconnect(websocket)

def start():
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8090, log_level="info")

if __name__ == "__main__":
    start()



@app.get("/api/experiments/meta")
async def experiments_meta():
    """Return experiment metadata from tracker files (includes sleeping)."""
    import json as _json
    from pathlib import Path

    exp_dir = Path("output/live/experiments")
    if not exp_dir.exists():
        return []

    result = []
    for exp_path in sorted(exp_dir.iterdir()):
        if not exp_path.is_dir():
            continue
        exp_file = exp_path / "experiment.json"
        if not exp_file.exists():
            continue
        try:
            meta = _json.loads(exp_file.read_text())
            sessions_file = exp_path / "investment_sessions.json"
            sessions = []
            if sessions_file.exists():
                sessions = _json.loads(sessions_file.read_text())
            result.append({
                "exp_id": meta.get("experiment_id", exp_path.name),
                "name": meta.get("name", ""),
                "status": meta.get("status", "unknown"),
                "created_at": meta.get("created_at", ""),
                "sessions": len(sessions),
            })
        except Exception:
            pass

    return result
