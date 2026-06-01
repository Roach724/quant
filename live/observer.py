"""Observer — passive recording of trading activity.

Writes trades, signals, position snapshots, equity curve to CSV/JSON/log.
Never throws — failures are logged but never propagate to the main loop.
"""
from __future__ import annotations

import csv
import logging
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)


class Observer:
    """Passive observer that records trading state to disk."""

    def __init__(self, output_dir: str, snapshot_interval: int = 60):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.snapshot_interval = snapshot_interval
        self._last_snapshot = None
        self._bar_count = 0

        # Open file handles in append mode — write headers only if file is empty.
        # This supports multi-day runs that reuse the same output directory.
        self._trades_file, self._trades_writer = self._open_log(
            "trades.csv", ["time", "symbol", "side", "qty", "price", "commission"]
        )
        self._signals_file, self._signals_writer = self._open_log(
            "signals.csv", ["time", "symbol", "side", "score", "rank"]
        )
        self._snapshots_file, self._snapshots_writer = self._open_log(
            "positions_snapshot.csv",
            ["timestamp", "symbol", "qty", "price", "cost_basis", "mkt_value", "pnl_pct"],
        )
        self._equity_file, self._equity_writer = self._open_log(
            "equity_curve.csv",
            ["timestamp", "equity", "cash", "portfolio_value", "return_pct"],
        )

        self._alert_file = open(self.output_dir / "alerts.log", "a")
        self._initial_equity = None

    def _open_log(self, filename: str, headers: list[str]):
        """Open a CSV log file in append mode, writing headers only if empty."""
        path = self.output_dir / filename
        is_new = not path.exists() or path.stat().st_size == 0
        f = open(path, "a", newline="")
        writer = csv.writer(f)
        if is_new:
            writer.writerow(headers)
        return f, writer

    def snapshot_due(self, now: datetime) -> bool:
        """Check if enough time has passed since last snapshot."""
        if self._last_snapshot is None:
            return True
        return (now - self._last_snapshot).total_seconds() >= self.snapshot_interval

    def record_signal(self, timestamp, symbol: str, side: str, score: float = 0.0, rank: int = 0):
        """Record a strategy signal."""
        try:
            self._signals_writer.writerow([timestamp, symbol, side, score, rank])
        except Exception:
            logger.exception("Observer: failed to record signal")

    def record_trade(self, timestamp, symbol: str, side: str, qty: int, price: float, commission: float = 0.0):
        """Record a filled trade."""
        try:
            self._trades_writer.writerow([timestamp, symbol, side, qty, price, commission])
        except Exception:
            logger.exception("Observer: failed to record trade")

    def record_bar(self, timestamp, equity: float, cash: float, return_pct: float = 0.0):
        """Record equity curve data point."""
        try:
            portfolio_value = equity
            if self._initial_equity is None:
                self._initial_equity = equity
            self._equity_writer.writerow([timestamp, equity, cash, portfolio_value, return_pct])
            self._bar_count += 1
        except Exception:
            logger.exception("Observer: failed to record bar")

    def snapshot_portfolio(self, timestamp, positions: list[dict]):
        """Write current position snapshot.
        
        positions: list of dicts with keys {symbol, qty, price, cost_basis, mkt_value, pnl_pct}
        """
        try:
            self._last_snapshot = timestamp
            for pos in positions:
                self._snapshots_writer.writerow([
                    timestamp,
                    pos.get("symbol", ""),
                    pos.get("qty", 0),
                    pos.get("price", 0.0),
                    pos.get("cost_basis", 0.0),
                    pos.get("mkt_value", 0.0),
                    pos.get("pnl_pct", 0.0),
                ])
        except Exception:
            logger.exception("Observer: failed to snapshot portfolio")

    def record_alert(self, timestamp, level: str, message: str):
        """Write an alert."""
        try:
            self._alert_file.write(f"{timestamp} [{level}] {message}\n")
        except Exception:
            logger.exception("Observer: failed to record alert")

    def close(self):
        """Close all file handles."""
        for f in [self._trades_file, self._signals_file, self._snapshots_file, self._equity_file, self._alert_file]:
            try:
                f.close()
            except Exception:
                pass
