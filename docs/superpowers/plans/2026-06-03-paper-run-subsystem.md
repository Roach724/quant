# Paper Run Subsystem Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a standalone paper trading backtest subsystem (CLI entry → run → compute metrics → write BQ → dashboard display)

**Architecture:** PaperRunRunner wraps the existing LiveRunner paper loop, captures equity curve + trades, computes performance metrics (Sharpe, MaxDD, annual return, etc.), and writes structured results to two new BQ tables (`paper_runs`, `paper_metrics`). Dashboard gets a new "Paper Run" tab with run selector, metrics cards, and equity/drawdown charts. Reuses existing `experiment_equity` and `experiment_trades` BQ tables for raw data.

**Tech Stack:** Python 3.12, BigQuery (insert_rows_json), FastAPI, Vue 3 (SPA), Chart.js (candlestick/line charts)

**Files:**
- Create: `paper_run/__init__.py`, `paper_run/runner.py`, `paper_run/metrics.py`, `paper_run/cli.py`
- Modify: `dashboard/server.py`, `dashboard/index.html`
- BQ: Create tables `paper_runs`, `paper_metrics` (schema via DashboardObserver pattern)

---

### Task 1: Create `paper_run/metrics.py`

**Files:**
- Create: `paper_run/__init__.py` (empty)
- Create: `paper_run/metrics.py`

- [ ] **Step 1: Write the metrics computation module**

Create `paper_run/__init__.py`:
```python
# paper_run — standalone paper trading backtest subsystem
```

Create `paper_run/metrics.py`:
```python
"""Performance metrics computed from equity curve data.

All functions accept a list of equity values (chronological) and return
a dict or scalar. Equity values are portfolio_value including cash.
"""

from __future__ import annotations

import math
from typing import Sequence


def compute_all_metrics(
    equity_series: Sequence[float],
    risk_free_rate: float = 0.02,
    periods_per_year: int = 252,
) -> dict:
    """Compute all standard performance metrics from an equity curve.

    Args:
        equity_series: chronological portfolio values (includes cash).
        risk_free_rate: annual risk-free rate (default 2%).
        periods_per_year: trading periods per year (252 for daily, 78 for 5m).

    Returns dict with keys:
        total_return, annual_return, annual_vol, sharpe, sortino,
        max_drawdown, calmar, win_rate, total_trades, profit_factor,
        start_equity, end_equity, n_periods
    """
    n = len(equity_series)
    if n < 2:
        return {
            "total_return": 0.0, "annual_return": 0.0, "annual_vol": 0.0,
            "sharpe": 0.0, "sortino": 0.0, "max_drawdown": 0.0,
            "calmar": 0.0, "win_rate": 0.0, "total_trades": 0,
            "profit_factor": 0.0, "start_equity": equity_series[0] if n else 0,
            "end_equity": equity_series[-1] if n else 0, "n_periods": n,
        }

    returns = _compute_returns(equity_series)
    total_return = (equity_series[-1] / equity_series[0]) - 1
    annual_return = _annualized_return(total_return, n, periods_per_year)
    annual_vol = _annualized_vol(returns, periods_per_year)
    sharpe = _sharpe_ratio(returns, risk_free_rate, periods_per_year, annual_vol)
    sortino = _sortino_ratio(returns, risk_free_rate, periods_per_year)
    max_dd = _max_drawdown(equity_series)
    calmar = annual_return / abs(max_dd) if max_dd != 0 else 0.0
    win_rate, total_trades, profit_factor = _trade_stats(returns)

    return {
        "total_return": round(total_return, 6),
        "annual_return": round(annual_return, 6),
        "annual_vol": round(annual_vol, 6),
        "sharpe": round(sharpe, 4),
        "sortino": round(sortino, 4),
        "max_drawdown": round(max_dd, 6),
        "calmar": round(calmar, 4),
        "win_rate": round(win_rate, 4),
        "total_trades": total_trades,
        "profit_factor": round(profit_factor, 4),
        "start_equity": round(equity_series[0], 2),
        "end_equity": round(equity_series[-1], 2),
        "n_periods": n,
    }


def _compute_returns(equity: Sequence[float]) -> list[float]:
    """Period-over-period returns."""
    return [(equity[i] / equity[i - 1]) - 1 for i in range(1, len(equity))]


def _annualized_return(total_return: float, n_periods: int, periods_per_year: int) -> float:
    """CAGR: (1 + total_return)^(periods_per_year / n_periods) - 1."""
    if n_periods == 0:
        return 0.0
    return (1 + total_return) ** (periods_per_year / n_periods) - 1


def _annualized_vol(returns: list[float], periods_per_year: int) -> float:
    """Std of returns * sqrt(periods_per_year)."""
    if len(returns) < 2:
        return 0.0
    mean_r = sum(returns) / len(returns)
    variance = sum((r - mean_r) ** 2 for r in returns) / (len(returns) - 1)
    return math.sqrt(variance) * math.sqrt(periods_per_year)


def _sharpe_ratio(
    returns: list[float], risk_free_rate: float,
    periods_per_year: int, annual_vol: float,
) -> float:
    """Sharpe = (annual_return - risk_free_rate) / annual_vol."""
    if annual_vol == 0 or len(returns) < 2:
        return 0.0
    n = len(returns)
    total_return = 1.0
    for r in returns:
        total_return *= (1 + r)
    annual_r = total_return ** (periods_per_year / n) - 1
    return (annual_r - risk_free_rate) / annual_vol


def _sortino_ratio(
    returns: list[float], risk_free_rate: float, periods_per_year: int,
) -> float:
    """Sortino = (annual_return - risk_free_rate) / downside_deviation."""
    if len(returns) < 2:
        return 0.0
    n = len(returns)
    total_return = 1.0
    for r in returns:
        total_return *= (1 + r)
    annual_r = total_return ** (periods_per_year / n) - 1

    # Downside deviation (only negative returns)
    period_rf = risk_free_rate / periods_per_year
    downside = [min(r - period_rf, 0) ** 2 for r in returns]
    if len(downside) < 2:
        return 0.0
    downside_std = math.sqrt(sum(downside) / (len(downside) - 1))
    downside_annual = downside_std * math.sqrt(periods_per_year)
    if downside_annual == 0:
        return 0.0
    return (annual_r - risk_free_rate) / downside_annual


def _max_drawdown(equity: Sequence[float]) -> float:
    """Maximum peak-to-trough decline as a negative fraction."""
    peak = equity[0]
    max_dd = 0.0
    for val in equity:
        if val > peak:
            peak = val
        dd = (val - peak) / peak
        if dd < max_dd:
            max_dd = dd
    return max_dd


def _trade_stats(returns: list[float]) -> tuple[float, int, float]:
    """Win rate, total trades (periods with non-zero return), profit factor."""
    wins = sum(1 for r in returns if r > 0)
    losses = sum(1 for r in returns if r < 0)
    total = wins + losses
    win_rate = wins / total if total > 0 else 0.0

    gross_profit = sum(r for r in returns if r > 0)
    gross_loss = abs(sum(r for r in returns if r < 0))
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else 0.0

    return win_rate, total, profit_factor
```

- [ ] **Step 2: Run a quick smoke test**

```bash
cd /opt/quant-prod && .venv/bin/python3 -c "
import sys; sys.path.insert(0, '.')
from paper_run.metrics import compute_all_metrics

# Test with simple equity curve (10% gain over 100 days)
equity = [100000 * (1 + 0.001 * i) for i in range(100)]
m = compute_all_metrics(equity)
for k, v in sorted(m.items()):
    print(f'  {k}: {v}')
"
```
Expected: Sharpe > 0, max_drawdown ≈ 0, annual_return ≈ 28% (100 days of 0.1%/day).

- [ ] **Step 3: Run pytest for metrics**

```bash
cd /opt/quant-dev && .venv/bin/python3 -m pytest tests/test_paper_metrics.py -v
```
If test file doesn't exist yet, create `tests/test_paper_metrics.py`:
```python
"""Tests for paper_run/metrics.py"""
import sys; sys.path.insert(0, '.')
from paper_run.metrics import compute_all_metrics, _max_drawdown


def test_flat_equity():
    """Flat equity should give zero return, zero vol, zero sharpe."""
    m = compute_all_metrics([100000] * 50)
    assert m["total_return"] == 0.0
    assert m["annual_return"] == 0.0
    assert m["sharpe"] == 0.0
    assert m["max_drawdown"] == 0.0


def test_steady_growth():
    """Steady 0.1% per period should give positive Sharpe, zero drawdown."""
    equity = [100000 * (1 + 0.001) ** i for i in range(252)]
    m = compute_all_metrics(equity, periods_per_year=252)
    assert m["total_return"] > 0
    assert m["sharpe"] > 0
    assert m["max_drawdown"] == 0.0


def test_drawdown():
    """Equity that drops 10% then recovers."""
    equity = [100, 90, 95, 100]
    dd = _max_drawdown(equity)
    assert dd == -0.1


def test_losing_equity():
    """Steady losses should give negative Sharpe."""
    equity = [100000 * (1 - 0.002) ** i for i in range(100)]
    m = compute_all_metrics(equity, periods_per_year=252)
    assert m["total_return"] < 0
    assert m["sharpe"] < 0
    assert m["max_drawdown"] < 0


def test_single_point():
    """Single data point should return zeros."""
    m = compute_all_metrics([100000])
    assert m["sharpe"] == 0.0
    assert m["n_periods"] == 1
```

- [ ] **Step 4: Commit**

```bash
cd /opt/quant-dev
git add paper_run/__init__.py paper_run/metrics.py tests/test_paper_metrics.py
git commit -m "feat: paper_run metrics — Sharpe, MaxDD, Sortino, CAGR, Calmar, etc."
```

---

### Task 2: Create `paper_run/runner.py`

**Files:**
- Create: `paper_run/runner.py`
- Modify: `paper_run/__init__.py` (add import)

- [ ] **Step 1: Write the PaperRunRunner class**

Create `paper_run/runner.py`:
```python
"""PaperRunRunner — standalone paper trading backtest engine.

Wraps the existing LiveRunner paper loop, captures equity/trade data,
computes performance metrics, and writes structured results to BigQuery.

Usage:
    from paper_run.runner import PaperRunRunner
    runner = PaperRunRunner("configs/paper_us.yaml")
    result = runner.run()  # dict with run_id, metrics, etc.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
from google.cloud import bigquery
from google.api_core import exceptions as gapi_exceptions

from live.runner import LiveRunner
from live.config import load_config
from paper_run.metrics import compute_all_metrics
from common.bq_writer import write_rows_to_bq

logger = logging.getLogger(__name__)

PROJECT = "deductive-notch-495015-c2"
DATASET = "quant"
TABLE_PAPER_RUNS = "paper_runs"
TABLE_PAPER_METRICS = "paper_metrics"

PAPER_RUNS_SCHEMA: list[bigquery.SchemaField] = [
    bigquery.SchemaField("run_id", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("name", "STRING", mode="NULLABLE"),
    bigquery.SchemaField("strategy", "STRING", mode="NULLABLE"),
    bigquery.SchemaField("market", "STRING", mode="NULLABLE"),
    bigquery.SchemaField("status", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("start_date", "DATE", mode="NULLABLE"),
    bigquery.SchemaField("end_date", "DATE", mode="NULLABLE"),
    bigquery.SchemaField("n_periods", "INTEGER", mode="NULLABLE"),
    bigquery.SchemaField("config_json", "STRING", mode="NULLABLE"),
    bigquery.SchemaField("error_msg", "STRING", mode="NULLABLE"),
    bigquery.SchemaField("created_at", "TIMESTAMP", mode="REQUIRED"),
]

PAPER_METRICS_SCHEMA: list[bigquery.SchemaField] = [
    bigquery.SchemaField("run_id", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("total_return", "FLOAT64", mode="NULLABLE"),
    bigquery.SchemaField("annual_return", "FLOAT64", mode="NULLABLE"),
    bigquery.SchemaField("annual_vol", "FLOAT64", mode="NULLABLE"),
    bigquery.SchemaField("sharpe", "FLOAT64", mode="NULLABLE"),
    bigquery.SchemaField("sortino", "FLOAT64", mode="NULLABLE"),
    bigquery.SchemaField("max_drawdown", "FLOAT64", mode="NULLABLE"),
    bigquery.SchemaField("calmar", "FLOAT64", mode="NULLABLE"),
    bigquery.SchemaField("win_rate", "FLOAT64", mode="NULLABLE"),
    bigquery.SchemaField("total_trades", "INTEGER", mode="NULLABLE"),
    bigquery.SchemaField("profit_factor", "FLOAT64", mode="NULLABLE"),
    bigquery.SchemaField("start_equity", "FLOAT64", mode="NULLABLE"),
    bigquery.SchemaField("end_equity", "FLOAT64", mode="NULLABLE"),
    bigquery.SchemaField("computed_at", "TIMESTAMP", mode="REQUIRED"),
]


def _table_ref(table: str) -> str:
    return f"{PROJECT}.{DATASET}.{table}"


def _ensure_table(client: bigquery.Client, table_name: str, schema: list) -> None:
    """Create BQ table if it doesn't exist."""
    ref = _table_ref(table_name)
    try:
        client.get_table(ref)
    except gapi_exceptions.NotFound:
        t = bigquery.Table(ref, schema=schema)
        t.clustering_fields = ["run_id"]
        client.create_table(t)
        logger.info("Created table %s", ref)


class PaperRunRunner:
    """Run a paper trading backtest and record structured results to BQ."""

    def __init__(self, config_path: str):
        self.config_path = Path(config_path)
        self.config = load_config(str(self.config_path))
        self.run_id = self.config.get("experiment", {}).get("id", uuid.uuid4().hex[:12])
        self._bq = bigquery.Client(project=PROJECT)
        _ensure_table(self._bq, TABLE_PAPER_RUNS, PAPER_RUNS_SCHEMA)
        _ensure_table(self._bq, TABLE_PAPER_METRICS, PAPER_METRICS_SCHEMA)

    def run(self) -> dict[str, Any]:
        """Run backtest, compute metrics, write to BQ. Returns result dict."""
        started_at = datetime.now(timezone.utc)
        result = {
            "run_id": self.run_id,
            "status": "running",
            "metrics": None,
            "error": None,
        }

        # 1. Record run as "running"
        self._write_run_status("running", started_at)

        # 2. Run the paper loop
        try:
            live_runner = LiveRunner(str(self.config_path), config=self.config)
            live_runner.run()
        except Exception as e:
            logger.exception("Paper run failed: %s", e)
            self._write_run_status("failed", started_at, error=str(e))
            result["status"] = "failed"
            result["error"] = str(e)
            return result

        # 3. Load equity curve from observer output
        output_dir = Path(self.config.get("live", {}).get("output_dir", "output/live/"))
        equity_path = output_dir / "equity_curve.csv"
        if not equity_path.exists():
            logger.error("No equity curve found at %s", equity_path)
            self._write_run_status("failed", started_at, error="No equity_curve.csv")
            result["status"] = "failed"
            result["error"] = "No equity_curve.csv"
            return result

        equity_df = pd.read_csv(equity_path)
        equity_series = equity_df["portfolio_value"].tolist()

        if len(equity_series) < 2:
            logger.warning("Too few data points (%d) for metrics", len(equity_series))
            self._write_run_status("completed", started_at, n_periods=len(equity_series))
            result["status"] = "completed"
            return result

        # 4. Compute metrics
        strategy_cfg = self.config.get("strategy", {})
        freq = strategy_cfg.get("frequency", "1d")
        periods_per_year = 252 if freq == "1d" else 78  # 78 = 390min / 5min
        metrics = compute_all_metrics(equity_series, periods_per_year=periods_per_year)

        # 5. Write metrics to BQ
        self._write_metrics(metrics)
        self._write_run_status("completed", started_at, n_periods=metrics["n_periods"])

        result["status"] = "completed"
        result["metrics"] = metrics
        logger.info("Paper run %s complete: Sharpe=%.2f, MaxDD=%.2f%%, Return=%.2f%%",
                     self.run_id, metrics["sharpe"],
                     metrics["max_drawdown"] * 100, metrics["total_return"] * 100)
        return result

    def _write_run_status(
        self, status: str, started_at: datetime,
        n_periods: int | None = None, error: str | None = None,
    ) -> None:
        """Write/update paper_runs row."""
        rows = [{
            "run_id": self.run_id,
            "name": self.config.get("experiment", {}).get("name", self.run_id),
            "strategy": self.config.get("strategy", {}).get("name", "unknown"),
            "market": self.config.get("live", {}).get("market", "unknown"),
            "status": status,
            "start_date": None,
            "end_date": None,
            "n_periods": n_periods,
            "config_json": json.dumps(self.config, default=str),
            "error_msg": error,
            "created_at": started_at.isoformat(),
        }]
        write_rows_to_bq(pd.DataFrame(rows), table_name=TABLE_PAPER_RUNS)

    def _write_metrics(self, metrics: dict) -> None:
        """Write computed metrics to paper_metrics table."""
        rows = [{
            "run_id": self.run_id,
            **metrics,
            "computed_at": datetime.now(timezone.utc).isoformat(),
        }]
        write_rows_to_bq(pd.DataFrame(rows), table_name=TABLE_PAPER_METRICS)
```

- [ ] **Step 2: Verify imports work**

```bash
cd /opt/quant-prod && .venv/bin/python3 -c "
from paper_run.runner import PaperRunRunner
print('PaperRunRunner imported OK')
print('Schemas defined:')
from paper_run.runner import PAPER_RUNS_SCHEMA, PAPER_METRICS_SCHEMA
for f in PAPER_RUNS_SCHEMA:
    print(f'  paper_runs.{f.name}: {f.field_type}')
"
```

- [ ] **Step 3: Commit**

```bash
cd /opt/quant-dev
git add paper_run/runner.py
git commit -m "feat: PaperRunRunner — run backtest, compute metrics, write BQ"
```

---

### Task 3: Create `paper_run/cli.py`

**Files:**
- Create: `paper_run/cli.py`

- [ ] **Step 1: Write CLI entry point**

Create `paper_run/cli.py`:
```python
"""CLI entry point for paper trading backtests.

Usage:
    python -m paper_run --config configs/paper_us.yaml
    python -m paper_run --config configs/paper_us.yaml --dry-run

Env vars:
    PAPER_RUN_CONFIG: path to config YAML (alternative to --config)
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

# Add project root to path so imports work when run as module
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from paper_run.runner import PaperRunRunner


def main():
    parser = argparse.ArgumentParser(description="Paper Trading Backtest Runner")
    parser.add_argument(
        "--config", "-c",
        default=os.environ.get("PAPER_RUN_CONFIG", ""),
        help="Path to config YAML file",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate config only, don't run",
    )
    args = parser.parse_args()

    if not args.config:
        parser.error("--config is required (or set PAPER_RUN_CONFIG env var)")

    config_path = Path(args.config)
    if not config_path.exists():
        print(f"Config not found: {config_path}", file=sys.stderr)
        sys.exit(1)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    runner = PaperRunRunner(str(config_path))
    run_id = runner.run_id
    strategy = runner.config.get("strategy", {}).get("name", "?")
    market = runner.config.get("live", {}).get("market", "?")

    if args.dry_run:
        print(f"Dry run OK — run_id={run_id} strategy={strategy} market={market}")
        return

    print(f"Starting paper run: {run_id} ({strategy}, {market})")
    result = runner.run()

    if result["status"] == "completed":
        m = result.get("metrics", {})
        print(f"✅ Paper run complete: {run_id}")
        print(f"   Sharpe: {m.get('sharpe', 'N/A')}")
        print(f"   Max DD: {m.get('max_drawdown', 0) * 100:.2f}%")
        print(f"   Annual Return: {m.get('annual_return', 0) * 100:.2f}%")
        print(f"   Total Return: {m.get('total_return', 0) * 100:.2f}%")
    else:
        print(f"❌ Paper run failed: {result.get('error', 'unknown')}")
        sys.exit(1)


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Verify CLI help**

```bash
cd /opt/quant-prod && .venv/bin/python3 -m paper_run --help
```
Expected: prints usage with --config and --dry-run flags.

- [ ] **Step 3: Dry-run test with existing config**

```bash
cd /opt/quant-prod && .venv/bin/python3 -m paper_run --config live/configs/paper_simple.yaml --dry-run
```
Expected: "Dry run OK — run_id=..."

- [ ] **Step 4: Commit**

```bash
cd /opt/quant-dev
git add paper_run/cli.py
git commit -m "feat: paper_run CLI — python -m paper_run --config xxx.yaml"
```

---

### Task 4: Extend Dashboard API (`server.py`)

**Files:**
- Modify: `dashboard/server.py` — add 4 endpoints after existing experiment APIs

- [ ] **Step 1: Add paper run API endpoints**

Add the following code block after the existing `trades` endpoint (around line 170 in server.py):

```python
# ── Paper Run APIs ──

@app.get("/api/paper-runs")
async def paper_runs(limit: int = 50, status: str | None = None):
    """List paper runs, most recent first. Optional status filter."""
    client = _get_bq()
    try:
        where = ""
        if status:
            where = f"WHERE status = '{status}'"
        query = f"""
            SELECT run_id, name, strategy, market, status, n_periods,
                   created_at, error_msg
            FROM {_table("paper_runs")}
            {where}
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
        # Run metadata
        run_query = f"""
            SELECT run_id, name, strategy, market, status, n_periods,
                   config_json, created_at, error_msg
            FROM {_table("paper_runs")}
            WHERE run_id = '{run_id}'
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
                m_names = [f.name for f in m_rows[0].fields]
                metrics = _row_to_dict(m_rows[0], m_names)
        except Exception:
            pass

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
        except Exception:
            pass

        return {"run": run, "metrics": metrics, "equity": equity, "trades": trades}
    except Exception as e:
        logger.error("paper_run_detail failed: %s", e)
        return {"error": str(e), "run_id": run_id}
```

- [ ] **Step 2: Restart dashboard server**

```bash
pkill -f "dashboard/server.py" 2>/dev/null
sleep 1
cd /opt/quant-prod && nohup .venv/bin/python3 dashboard/server.py --port 8090 > /dev/null 2>&1 &
sleep 2
curl -s http://localhost:8090/api/paper-runs | python3 -m json.tool | head -10
```
Expected: `[]` (empty list, tables just created).

- [ ] **Step 3: Commit**

```bash
cd /opt/quant-dev
git add dashboard/server.py
git commit -m "feat: Paper Run API endpoints — list, detail, equity, trades"
```

---

### Task 5: Add Paper Run Tab to Dashboard Frontend

**Files:**
- Modify: `dashboard/index.html` — add tab definition, template, and JS

- [ ] **Step 1: Add tab button**

Find the tabs definition (around line 674):
```javascript
{ id: 'overview', label: 'Overview' },
{ id: 'detail', label: 'Live Experiment' },
{ id: 'pipeline', label: 'Pipeline' },
{ id: 'alerts', label: 'Alerts' },
```

Add after `'detail'`:
```javascript
{ id: 'paper', label: 'Paper Run' },
```

- [ ] **Step 2: Add Paper Run template HTML**

After the Alerts tab template section (after the `<!-- Alerts Tab -->` comment block), add:

```html
<!-- ── Paper Run Tab ── -->
<template v-if="activeTab === 'paper'">
  <div class="paper-run-layout">
    <!-- Left: Run Selector -->
    <div class="paper-sidebar">
      <h3>📋 Paper Runs</h3>
      <select v-model="selectedPaperRun" @change="loadPaperRun" class="run-select">
        <option value="">— Select a run —</option>
        <option v-for="r in paperRuns" :key="r.run_id" :value="r.run_id">
          {{ r.name || r.run_id }} ({{ r.status }})
        </option>
      </select>
      <div class="run-meta" v-if="paperRun.run">
        <div><strong>Strategy:</strong> {{ paperRun.run.strategy }}</div>
        <div><strong>Market:</strong> {{ paperRun.run.market }}</div>
        <div><strong>Periods:</strong> {{ paperRun.run.n_periods }}</div>
        <div><strong>Created:</strong> {{ fmtTs(paperRun.run.created_at) }}</div>
      </div>
    </div>

    <!-- Right: Results -->
    <div class="paper-main" v-if="paperRun.run">
      <!-- Metrics Cards -->
      <div class="metrics-grid">
        <div class="metric-card">
          <div class="metric-label">Sharpe</div>
          <div class="metric-value" :class="metricColor(paperRun.metrics.sharpe, 0, 1, 2)">{{ fmtNum(paperRun.metrics.sharpe) }}</div>
        </div>
        <div class="metric-card">
          <div class="metric-label">Max Drawdown</div>
          <div class="metric-value red">{{ fmtPct(paperRun.metrics.max_drawdown) }}</div>
        </div>
        <div class="metric-card">
          <div class="metric-label">Annual Return</div>
          <div class="metric-value" :class="metricColor(paperRun.metrics.annual_return, 0, 0.1, 0.3)">{{ fmtPct(paperRun.metrics.annual_return) }}</div>
        </div>
        <div class="metric-card">
          <div class="metric-label">Annual Vol</div>
          <div class="metric-value">{{ fmtPct(paperRun.metrics.annual_vol) }}</div>
        </div>
        <div class="metric-card">
          <div class="metric-label">Sortino</div>
          <div class="metric-value">{{ fmtNum(paperRun.metrics.sortino) }}</div>
        </div>
        <div class="metric-card">
          <div class="metric-label">Calmar</div>
          <div class="metric-value">{{ fmtNum(paperRun.metrics.calmar) }}</div>
        </div>
        <div class="metric-card">
          <div class="metric-label">Win Rate</div>
          <div class="metric-value">{{ fmtPct(paperRun.metrics.win_rate) }}</div>
        </div>
        <div class="metric-card">
          <div class="metric-label">Profit Factor</div>
          <div class="metric-value">{{ fmtNum(paperRun.metrics.profit_factor) }}</div>
        </div>
      </div>

      <!-- Equity + Drawdown Chart -->
      <div class="chart-section">
        <h4>📈 Equity Curve</h4>
        <canvas id="paperEquityChart" width="800" height="300"></canvas>
      </div>

      <!-- Trade List -->
      <div class="trades-section" v-if="paperRun.trades.length">
        <h4>📊 Trades ({{ paperRun.trades.length }})</h4>
        <div class="trades-table-wrap">
          <table class="trades-table">
            <thead>
              <tr><th>Bar</th><th>Symbol</th><th>Side</th><th>Qty</th><th>Price</th><th>Commission</th></tr>
            </thead>
            <tbody>
              <tr v-for="t in paperRun.trades.slice(-50)" :key="t.bar + t.symbol">
                <td>{{ t.bar }}</td>
                <td>{{ t.symbol }}</td>
                <td><span :class="'side-' + t.side.toLowerCase()">{{ t.side }}</span></td>
                <td>{{ t.qty }}</td>
                <td>{{ fmtNum(t.price) }}</td>
                <td>{{ fmtNum(t.commission) }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>

      <div class="empty-state" v-else-if="paperRun.equity.length">
        <p>No trades recorded.</p>
      </div>
    </div>

    <div class="empty-state" v-else>
      <p>Select a paper run to view results.</p>
    </div>
  </div>
</template>
```

- [ ] **Step 3: Add Paper Run CSS styles**

Add to the `<style>` section (after the Alerts tab CSS):

```css
/* ── Paper Run Tab ── */
.paper-run-layout { display: flex; gap: 20px; }
.paper-sidebar { width: 280px; flex-shrink: 0; }
.paper-main { flex: 1; min-width: 0; }
.paper-sidebar h3 { font-size: 15px; margin-bottom: 12px; }
.run-select {
  width: 100%;
  padding: 8px 10px;
  background: var(--bg-card);
  border: 1px solid var(--border);
  border-radius: 6px;
  color: var(--text-primary);
  font-size: 13px;
  margin-bottom: 12px;
}
.run-meta { font-size: 12px; color: var(--text-secondary); line-height: 1.8; }

.metrics-grid {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 12px;
  margin-bottom: 20px;
}
.metric-card {
  background: var(--bg-card);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 14px 16px;
  text-align: center;
}
.metric-label { font-size: 11px; color: var(--text-secondary); text-transform: uppercase; letter-spacing: 0.5px; }
.metric-value { font-size: 22px; font-weight: 700; margin-top: 4px; font-variant-numeric: tabular-nums; }
.metric-value.red { color: var(--red); }
.metric-value.green { color: var(--green); }
.metric-value.neutral { color: var(--text-primary); }

.chart-section { margin-bottom: 20px; }
.chart-section h4 { margin-bottom: 8px; }
.trades-section h4 { margin-bottom: 8px; }
```

- [ ] **Step 4: Add Paper Run JS logic**

Add to the Vue app's `data()` function:
```javascript
paperRuns: [],
selectedPaperRun: '',
paperRun: { run: null, metrics: {}, equity: [], trades: [] },
```

Add methods (in the `methods` section):
```javascript
async loadPaperRuns() {
  try { this.paperRuns = await (await fetch('/api/paper-runs')).json(); }
  catch (e) { console.error('Paper runs load failed', e); }
},
async loadPaperRun() {
  if (!this.selectedPaperRun) { this.paperRun = { run: null, metrics: {}, equity: [], trades: [] }; return; }
  try {
    const r = await (await fetch('/api/paper-runs/' + this.selectedPaperRun)).json();
    this.paperRun = r;
    this.$nextTick(() => this.renderPaperEquityChart());
  } catch (e) { console.error('Paper run detail failed', e); }
},
renderPaperEquityChart() {
  const canvas = document.getElementById('paperEquityChart');
  if (!canvas || !this.paperRun.equity.length) return;
  const ctx = canvas.getContext('2d');
  if (this._paperChart) this._paperChart.destroy();
  const labels = this.paperRun.equity.map(e => e.bar);
  const equityData = this.paperRun.equity.map(e => e.portfolio_value);
  const ddData = this.paperRun.equity.map(e => e.drawdown * 100);
  this._paperChart = new Chart(ctx, {
    type: 'line',
    data: {
      labels,
      datasets: [
        { label: 'Equity', data: equityData, borderColor: '#4ade80', backgroundColor: 'rgba(74,222,128,0.05)', fill: true, tension: 0.1, yAxisID: 'y' },
        { label: 'Drawdown %', data: ddData, borderColor: '#f87171', backgroundColor: 'rgba(248,113,113,0.05)', fill: true, tension: 0.1, yAxisID: 'y1' },
      ]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { intersect: false, mode: 'index' },
      scales: {
        y: { type: 'linear', position: 'left', title: { display: true, text: 'Equity' } },
        y1: { type: 'linear', position: 'right', title: { display: true, text: 'Drawdown %' }, grid: { drawOnChartArea: false } },
      }
    }
  });
},
metricColor(val, low, mid, high) {
  if (val == null) return 'neutral';
  if (val >= high) return 'green';
  if (val >= mid) return 'neutral';
  if (val >= low) return 'red';
  return 'red';
},
fmtPct(v) { if (v == null) return '—'; return (v * 100).toFixed(2) + '%'; },
```

Add tab loading hook (alongside existing `if (tab === 'pipeline')`):
```javascript
if (tab === 'paper') { this.loadPaperRuns(); }
```

- [ ] **Step 5: Test the frontend**

```bash
curl -s http://localhost:8090/ | grep -o "Paper Run" | head -1
```
Expected: "Paper Run"

- [ ] **Step 6: Commit**

```bash
cd /opt/quant-dev
git add dashboard/index.html
git commit -m "feat: Paper Run dashboard tab — runs list, metrics, equity chart, trades"
```

---

### Task 6: End-to-End Test

**Files:** (none, verification only)

- [ ] **Step 1: Run a paper backtest with the CLI**

```bash
cd /opt/quant-prod && timeout 120 .venv/bin/python3 -m paper_run --config live/configs/paper_simple.yaml 2>&1
```
Expected: "Paper run complete: ..." with metrics.

- [ ] **Step 2: Verify BQ tables have data**

```bash
cd /opt/quant-prod && .venv/bin/python3 -c "
from google.cloud import bigquery
client = bigquery.Client()

# paper_runs
runs = list(client.query('SELECT run_id, status, strategy FROM quant.paper_runs ORDER BY created_at DESC LIMIT 3').result())
print('paper_runs:')
for r in runs: print(f'  {r.run_id} | {r.status} | {r.strategy}')

# paper_metrics
mets = list(client.query('SELECT run_id, sharpe, max_drawdown FROM quant.paper_metrics ORDER BY computed_at DESC LIMIT 3').result())
print('paper_metrics:')
for m in mets: print(f'  {m.run_id} | Sharpe={m.sharpe} MaxDD={m.max_drawdown}')
"
```

- [ ] **Step 3: Verify API returns data**

```bash
curl -s http://localhost:8090/api/paper-runs | python3 -c "import sys,json; d=json.load(sys.stdin); print(f'{len(d)} runs'); [print(f'  {r.get(\"run_id\")} — {r.get(\"status\")}') for r in d[:3]]"
```

- [ ] **Step 4: Update MEMORY.md**

Add project entry to MEMORY.md under "量化交易系统":
```
| 🧪 Paper Run | ✅ | CLI + BQ + Dashboard tab, 6 metrics + equity/drawdown chart |
```

---

## Self-Review

**1. Spec coverage:**
- [x] 回测入口 → Task 3 (CLI)
- [x] 写回测结果到 BQ → Task 2 (runner.py writes paper_runs + paper_metrics)
- [x] 前端展示指标（夏普/最大回撤/年化收益）→ Task 5 (metrics cards)
- [x] 图表 → Task 5 (equity + drawdown Chart.js)

**2. Placeholder scan:** No TODOs, TBDs, or vague instructions. Every step has concrete code.

**3. Type consistency:** `run_id` used consistently across all files and BQ schemas. `PaperRunRunner.run()` returns `dict[str, Any]` with consistent keys.
