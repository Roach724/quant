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
async def experiments(type: str = ""):
    """Return the most-recent equity snapshot for every distinct experiment.
    
    Optional query params:
        type: filter by experiment type prefix (e.g. "live", "paper", "sim").
              Uses LIKE '{type}_%' to match all experiments of that category.
    """
    client = _get_bq()
    prefix_filter = ""
    if type:
        prefix_filter = f"AND exp_id LIKE '{type}_%'"
    query = f"""
        SELECT * EXCEPT (rn)
        FROM (
            SELECT *,
                   ROW_NUMBER() OVER (PARTITION BY exp_id ORDER BY ts DESC) AS rn
            FROM {_table("experiment_equity")}
            WHERE NOT STARTS_WITH(exp_id, "test_") {prefix_filter}
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
async def equity_series(exp_id: str, run_id: str = ""):
    """Return the full equity curve for a single experiment (ordered by bar).
    
    Optional query params:
        run_id: filter by specific run. Useful for experiments with multiple runs.
    """
    client = _get_bq()
    if not run_id:
        # Default to latest run — avoid showing all historical runs overlapped
        latest_q = f"""
            SELECT run_id FROM {_table("experiment_equity")}
            WHERE exp_id = @exp_id
            ORDER BY bar DESC LIMIT 1
        """
        latest_rows = list(client.query(latest_q, job_config=bigquery.QueryJobConfig(
            query_parameters=[bigquery.ScalarQueryParameter("exp_id", "STRING", exp_id)]
        )).result())
        if latest_rows:
            run_id = latest_rows[0].run_id or ""
    run_filter = f"AND run_id = '{run_id}'" if run_id else ""
    query = f"""
        SELECT ts, bar, equity, cash, portfolio_value, daily_pnl, drawdown, run_id
        FROM {_table("experiment_equity")}
        WHERE exp_id = @exp_id {run_filter}
        ORDER BY bar ASC
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[bigquery.ScalarQueryParameter("exp_id", "STRING", exp_id)]
    )
    try:
        rows = client.query(query, job_config=job_config).result()
        return [_row_to_dict(r, ["ts", "bar", "equity", "cash",
                                  "portfolio_value", "daily_pnl", "drawdown", "run_id"])
                for r in rows]
    except Exception as exc:
        logger.error("equity_series query error for %s: %s", exp_id, exc)
        return []


# ---------------------------------------------------------------------------
# GET /api/trades/{exp_id} — recent trades for one experiment
# ---------------------------------------------------------------------------
@app.get("/api/trades/{exp_id}")
async def trades(exp_id: str, limit: int = 200, run_id: str = ""):
    """Return the most-recent trades for an experiment.
    
    Optional query params:
        run_id: filter by specific run. Defaults to latest run.
        limit: max number of trades to return (default 200).
    """
    client = _get_bq()
    if not run_id:
        latest_q = f"""
            SELECT run_id FROM {_table("experiment_trades")}
            WHERE exp_id = '{exp_id}'
            ORDER BY ts DESC LIMIT 1
        """
        latest_rows = list(client.query(latest_q).result())
        if latest_rows:
            run_id = latest_rows[0].run_id or ""
    run_filter = f"AND run_id = @run_id" if run_id else ""
    query = f"""
        SELECT ts, bar, symbol, side, qty, price, commission
        FROM {_table("experiment_trades")}
        WHERE exp_id = @exp_id {run_filter}
        ORDER BY ts DESC
        LIMIT @limit
    """
    params = [
        bigquery.ScalarQueryParameter("exp_id", "STRING", exp_id),
        bigquery.ScalarQueryParameter("limit", "INT64", limit),
    ]
    if run_id:
        params.append(bigquery.ScalarQueryParameter("run_id", "STRING", run_id))
    job_config = bigquery.QueryJobConfig(query_parameters=params)
    try:
        rows = client.query(query, job_config=job_config).result()
        result = []
        for r in rows:
            d = _row_to_dict(r, ["ts", "bar", "symbol", "side",
                                  "qty", "price", "commission"])
            # Pad HK symbols to 5 digits (stored bare, display with leading zeros)
            if "hk" in exp_id and d.get("symbol"):
                d["symbol"] = d["symbol"].zfill(5)
            result.append(d)
        return result
    except Exception as exc:
        logger.error("trades query error for %s: %s", exp_id, exc)
        return []


# ---------------------------------------------------------------------------
# GET /api/experiments/{exp_id}/runs — list all runs for an experiment
# ---------------------------------------------------------------------------
@app.get("/api/experiments/{exp_id}/runs")
async def experiment_runs(exp_id: str):
    """Return all runs for a given experiment (metadata from experiment_runs table)."""
    client = _get_bq()
    query = f"""
        SELECT run_id, status, started_at, ended_at, base_run
        FROM {_table("experiment_runs")}
        WHERE exp_id = '{exp_id}'
        ORDER BY started_at DESC
    """
    try:
        rows = client.query(query).result()
        return [_row_to_dict(r, ["run_id", "status", "started_at", "ended_at", "base_run"])
                for r in rows]
    except Exception as exc:
        logger.error("experiment_runs query error for %s: %s", exp_id, exc)
        return []


# ── Paper Run APIs ──

@app.get("/api/paper-runs")
async def paper_runs(limit: int = 50, status: str | None = None):
    """List paper runs, most recent first. Optional status filter."""
    client = _get_bq()
    try:
        # Dedup: return only the latest status per run_id
        query = f"""
            SELECT run_id, name, strategy, market, status, n_periods,
                   created_at, error_msg
            FROM (
              SELECT *, ROW_NUMBER() OVER (PARTITION BY run_id ORDER BY created_at DESC) AS rn
              FROM {_table("paper_runs")}
            )
            WHERE rn = 1
            ORDER BY created_at DESC
            LIMIT {min(limit, 200)}
        """
        rows = client.query(query).result()
        names = ["run_id", "name", "strategy", "market", "status",
                 "n_periods", "created_at", "error_msg"]
        return [_row_to_dict(r, names) for r in rows]
    except Exception as e:
        logger.error("paper_runs query failed: %s", e)
        return []


@app.get("/api/paper-runs/{run_id}")
async def paper_run_detail(run_id: str):
    """Detail for a single paper run: metadata + equity curve + trades.
    
    Returns: { run: {...}, metrics: {...}, equity: [...], trades: [...] }
    """
    client = _get_bq()
    try:
        # Run metadata (latest status by created_at)
        run_query = f"""
            SELECT run_id, name, strategy, market, status, n_periods,
                   config_json, created_at, error_msg
            FROM {_table("paper_runs")}
            WHERE run_id = '{run_id}'
            ORDER BY created_at DESC LIMIT 1
        """
        run_rows = list(client.query(run_query).result())
        if not run_rows:
            return {"error": "not found", "run_id": run_id}
        run_names = ["run_id", "name", "strategy", "market", "status",
                     "n_periods", "config_json", "created_at", "error_msg"]
        run = _row_to_dict(run_rows[0], run_names)

        # Metrics
        metrics = {}
        try:
            m_query = f"""
                SELECT *
                FROM {_table("paper_metrics")}
                WHERE run_id = '{run_id}'
            """
            m_rows = list(client.query(m_query).result())
            if m_rows:
                m_names = ["run_id", "total_return", "annual_return", "annual_vol",
                          "sharpe", "sortino", "max_drawdown", "calmar",
                          "win_rate", "total_trades", "profit_factor",
                          "start_equity", "end_equity", "computed_at"]
                metrics = _row_to_dict(m_rows[0], m_names)
        except Exception as e:
            logger.exception("paper_metrics query failed")
            metrics = {"error": str(e)}

        # Equity curve (from experiment_equity)
        equity = []
        try:
            e_query = f"""
                SELECT ts, bar, equity, cash, portfolio_value, daily_pnl, drawdown
                FROM {_table("experiment_equity")}
                WHERE exp_id = '{run_id}'
                ORDER BY bar
            """
            e_rows = client.query(e_query).result()
            e_names = ["ts", "bar", "equity", "cash", "portfolio_value", "daily_pnl", "drawdown"]
            equity = [_row_to_dict(r, e_names) for r in e_rows]
        except Exception:
            pass

        # Trades
        trades = []
        try:
            t_query = f"""
                SELECT ts, bar, symbol, side, qty, price, commission
                FROM {_table("experiment_trades")}
                WHERE exp_id = '{run_id}'
                ORDER BY bar
                LIMIT 500
            """
            t_rows = client.query(t_query).result()
            t_names = ["ts", "bar", "symbol", "side", "qty", "price", "commission"]
            trades = [_row_to_dict(r, t_names) for r in t_rows]
            # Pad HK symbols to 5 digits
            if run.get("market", "").lower() == "hk":
                for t in trades:
                    if t.get("symbol"):
                        t["symbol"] = t["symbol"].zfill(5)
        except Exception:
            pass

        return {"run": run, "metrics": metrics, "equity": equity, "trades": trades}
    except Exception as e:
        logger.error("paper_run_detail failed: %s", e)
        return {"error": str(e), "run_id": run_id}


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
            SELECT MAX(
              TIMESTAMP(DATETIME(timestamp), "America/New_York")
            ) AS latest FROM {_table("us_bars_5m")}
            WHERE timestamp >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 24 HOUR)
        """
        rows = list(client.query(q).result())
        if rows and rows[0].latest:
            result["us"] = _serialize(rows[0].latest)
    except Exception as exc:
        logger.error("pipeline us query error: %s", exc)

    try:
        q = f"""
            SELECT MAX(
              TIMESTAMP_SUB(timestamp, INTERVAL 8 HOUR)
            ) AS latest FROM {_table("hk_bars_5m")}
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


@app.get("/api/market/symbols/{market}")
async def market_symbols(market: str):
    """Return symbol list for a market from symbols.yaml."""
    import yaml
    from pathlib import Path
    config_path = Path("/opt/quant-dev/config/symbols.yaml")
    cfg = yaml.safe_load(config_path.read_text())
    syms = cfg.get("markets", {}).get(market, {}).get("symbols", [])
    prefix = f"{'US' if market == 'us' else 'HK'}."
    return [s.replace(prefix, "") for s in syms if s.startswith(prefix)]



@app.get("/api/experiments/{exp_id}/positions")
async def experiment_positions(exp_id: str, run_id: str = ""):
    """Compute current positions from all trades, with correct cost basis."""
    client = _get_bq()
    if not run_id:
        # Default to latest run
        latest_q = f"""
            SELECT run_id FROM {_table("experiment_trades")}
            WHERE exp_id = '{exp_id}'
            ORDER BY ts DESC LIMIT 1
        """
        latest_rows = list(client.query(latest_q).result())
        if latest_rows:
            run_id = latest_rows[0].run_id or ""
    run_filter = f"AND run_id = '{run_id}'" if run_id else ""
    trades_q = f"""
        SELECT symbol, side, qty, price, ts
        FROM {_table("experiment_trades")}
        WHERE exp_id = '{exp_id}' {run_filter}
        ORDER BY ts
    """
    rows = list(client.query(trades_q).result())
    if not rows:
        return []
    
    # Track lots per symbol: each buy creates a lot, sells reduce from oldest lots
    from collections import defaultdict
    lots = defaultdict(list)  # symbol → [(qty, price)]
    
    for r in rows:
        sym = r.symbol
        qty = float(r.qty)
        price = float(r.price)
        if r.side == 'buy':
            lots[sym].append({'qty': qty, 'price': price})
        else:  # sell — reduce from oldest lots (FIFO)
            remaining = qty
            while remaining > 0 and lots[sym]:
                lot = lots[sym][0]
                if lot['qty'] <= remaining:
                    remaining -= lot['qty']
                    lots[sym].pop(0)
                else:
                    lot['qty'] -= remaining
                    remaining = 0
    
    if not lots:
        return []
    
    # Get current prices
    result = []
    for sym, sym_lots in lots.items():
        total_qty = sum(l['qty'] for l in sym_lots)
        if total_qty <= 0:
            continue
        total_cost = sum(l['qty'] * l['price'] for l in sym_lots)
        avg_cost = total_cost / total_qty
        
        us_prefix = sym.startswith('US.')
        # Detect market from experiment_id if symbol is bare
        if us_prefix:
            market = 'us'
            bare = sym[3:]
        elif sym.startswith('HK.'):
            market = 'hk'
            bare = sym[3:]
        else:
            # Bare symbol — infer from experiment
            market = 'hk' if 'hk' in exp_id else 'us'
            bare = sym
        prefix = 'US.' if market == 'us' else 'HK.'
        # HK symbols need 5-digit zero-padding (Futu format: HK.01186 not HK.1186)
        if market == 'hk':
            bare = bare.zfill(5)
        bq_sym = f"{prefix}{bare}"
        table = _table(f"{market}_bars_5m")
        try:
            price_q = f"""
                SELECT close FROM `{table}`
                WHERE symbol = '{bq_sym}'
                ORDER BY timestamp DESC LIMIT 1
            """
            price_rows = list(client.query(price_q).result())
            current_price = float(price_rows[0].close) if price_rows else avg_cost
        except Exception:
            current_price = avg_cost
        
        pnl = (current_price - avg_cost) * total_qty
        pnl_pct = (current_price - avg_cost) / avg_cost * 100 if avg_cost > 0 else 0
        result.append({
            "symbol": bare,
            "qty": round(total_qty, 2),
            "avg_cost": round(avg_cost, 2),
            "current_price": round(current_price, 2),
            "pnl": round(pnl, 2),
            "pnl_pct": round(pnl_pct, 2),
        })
    return result


@app.get("/api/market/{market}/{symbol}")
async def market_bars(market: str, symbol: str, limit: int = 78):
    """Return today's 5m OHLCV bars for a symbol, with timestamps corrected to UTC."""
    client = _get_bq()
    table = _table(f"{market}_bars_5m")
    full_symbol = f"{'US' if market == 'us' else 'HK'}.{symbol}"

    # Timezone correction: bars stored as local time, convert to UTC
    if market == "hk":
        ts_expr = "TIMESTAMP_SUB(timestamp, INTERVAL 8 HOUR)"
    else:  # us
        ts_expr = 'TIMESTAMP(DATETIME(timestamp), "America/New_York")'

    query = f"""
        WITH dedup AS (
          SELECT {ts_expr} AS timestamp, open, high, low, close, volume,
            ROW_NUMBER() OVER (PARTITION BY symbol, timestamp ORDER BY _ingest_time DESC NULLS LAST) AS rn
          FROM `{table}`
          WHERE symbol = '{full_symbol}'
            AND {ts_expr} >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 2 DAY)
        )
        SELECT timestamp, open, high, low, close, volume
        FROM dedup WHERE rn = 1
        ORDER BY timestamp DESC
        LIMIT {limit}
    """
    rows = list(client.query(query).result())
    rows.reverse()  # oldest first for chart display
    return [{"ts": _serialize(r.timestamp), "o": r.open, "h": r.high,
             "l": r.low, "c": r.close, "v": r.volume} for r in rows]


