"""DashboardObserver — writes experiment equity & trade data to BigQuery.

Creates `experiment_equity` and `experiment_trades` tables on first use.
Uses streaming inserts (insert_rows_json) for near-real-time visibility.

Usage:
    from dashboard.observer import DashboardObserver

    observer = DashboardObserver(exp_id="backtest_20260603", market="us")
    observer.record_equity(bar=100, equity=105000, cash=5000,
                           portfolio_value=105000, daily_pnl=5000, drawdown=0.02)
    observer.record_trade(bar=100, symbol="AAPL", side="BUY",
                          qty=100, price=150.0, commission=1.0)
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from google.api_core import exceptions as gapi_exceptions
from google.cloud import bigquery

logger = logging.getLogger(__name__)

PROJECT = "deductive-notch-495015-c2"
DATASET = "quant"

TABLE_EQUITY = "experiment_equity"
TABLE_TRADES = "experiment_trades"

EQUITY_SCHEMA: list[bigquery.SchemaField] = [
    bigquery.SchemaField("ts", "TIMESTAMP", mode="REQUIRED"),
    bigquery.SchemaField("exp_id", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("run_id", "STRING", mode="NULLABLE"),
    bigquery.SchemaField("bar", "INTEGER", mode="REQUIRED"),
    bigquery.SchemaField("equity", "FLOAT64", mode="REQUIRED"),
    bigquery.SchemaField("cash", "FLOAT64", mode="REQUIRED"),
    bigquery.SchemaField("portfolio_value", "FLOAT64", mode="REQUIRED"),
    bigquery.SchemaField("daily_pnl", "FLOAT64", mode="REQUIRED"),
    bigquery.SchemaField("drawdown", "FLOAT64", mode="REQUIRED"),
]

TRADES_SCHEMA: list[bigquery.SchemaField] = [
    bigquery.SchemaField("ts", "TIMESTAMP", mode="REQUIRED"),
    bigquery.SchemaField("exp_id", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("run_id", "STRING", mode="NULLABLE"),
    bigquery.SchemaField("bar", "INTEGER", mode="REQUIRED"),
    bigquery.SchemaField("symbol", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("side", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("qty", "FLOAT64", mode="REQUIRED"),
    bigquery.SchemaField("price", "FLOAT64", mode="REQUIRED"),
    bigquery.SchemaField("commission", "FLOAT64", mode="REQUIRED"),
]


def _table_ref(table: str) -> str:
    return f"{PROJECT}.{DATASET}.{table}"


def _ensure_table(client: bigquery.Client, table_name: str, schema: list[bigquery.SchemaField]) -> None:
    """Create the BQ table if it does not already exist."""
    table_ref = _table_ref(table_name)
    try:
        client.get_table(table_ref)
        logger.debug("Table %s already exists", table_ref)
    except gapi_exceptions.NotFound:
        table = bigquery.Table(table_ref, schema=schema)
        # clustering on exp_id for efficient per-experiment queries
        table.clustering_fields = ["exp_id"]
        # partition by day on ts for cost control
        table.time_partitioning = bigquery.TimePartitioning(
            type_=bigquery.TimePartitioningType.DAY,
            field="ts",
        )
        client.create_table(table)
        logger.info("Created table %s (partitioned by ts, clustered by exp_id)", table_ref)


class DashboardObserver:
    """Writes experiment equity snapshots and trades to BigQuery tables.

    Tables are auto-created on first instantiation per process.
    """

    def __init__(self, exp_id: str, market: str) -> None:
        self.exp_id = exp_id
        self.market = market
        self._client = bigquery.Client(project=PROJECT)

        # Ensure tables exist
        _ensure_table(self._client, TABLE_EQUITY, EQUITY_SCHEMA)
        _ensure_table(self._client, TABLE_TRADES, TRADES_SCHEMA)

        logger.info(
            "DashboardObserver ready — exp_id=%s market=%s", self.exp_id, self.market
        )

    def record_equity(
        self,
        bar: int,
        equity: float,
        cash: float,
        portfolio_value: float,
        daily_pnl: float,
        drawdown: float,
        run_id: str = "",
    ) -> None:
        """Write one equity snapshot row to experiment_equity."""
        row = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "exp_id": self.exp_id,
            "run_id": run_id,
            "bar": bar,
            "equity": equity,
            "cash": cash,
            "portfolio_value": portfolio_value,
            "daily_pnl": daily_pnl,
            "drawdown": drawdown,
        }
        table_ref = _table_ref(TABLE_EQUITY)
        errors = self._client.insert_rows_json(table_ref, [row])
        if errors:
            logger.error("record_equity insert errors: %s", errors)
            raise RuntimeError(f"BQ insert error: {errors}")

    def record_trade(
        self,
        bar: int,
        symbol: str,
        side: str,
        qty: float,
        price: float,
        commission: float,
        run_id: str = "",
    ) -> None:
        """Write one trade row to experiment_trades."""
        row = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "exp_id": self.exp_id,
            "run_id": run_id,
            "bar": bar,
            "symbol": symbol,
            "side": side,
            "qty": qty,
            "price": price,
            "commission": commission,
        }
        table_ref = _table_ref(TABLE_TRADES)
        errors = self._client.insert_rows_json(table_ref, [row])
        if errors:
            logger.error("record_trade insert errors: %s", errors)
            raise RuntimeError(f"BQ insert error: {errors}")
