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
        # LiveRunner sets config["_output_dir"] to the timestamped subdirectory
        output_dir = Path(self.config.get("_output_dir", self.config.get("live", {}).get("output_dir", "output/live/")))
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
        """Write/update paper_runs row (DELETE old + INSERT new to dedup)."""
        # Delete existing rows for this run_id first
        try:
            self._bq.query(f"DELETE FROM {_table_ref(TABLE_PAPER_RUNS)} WHERE run_id = '{self.run_id}'").result()
        except Exception:
            pass
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
        # Only include fields that exist in the table schema
        schema_fields = {f.name for f in PAPER_METRICS_SCHEMA} - {"run_id", "computed_at"}
        filtered = {k: v for k, v in metrics.items() if k in schema_fields}
        rows = [{
            "run_id": self.run_id,
            **filtered,
            "computed_at": datetime.now(timezone.utc).isoformat(),
        }]
        write_rows_to_bq(pd.DataFrame(rows), table_name=TABLE_PAPER_METRICS)
