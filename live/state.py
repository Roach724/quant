"""StateManager — portfolio and live-state persistence for multi-day runs.

Saves/loads:
- Portfolio state (cash, positions with avg_entry/size/cost/realized_pnl)
- PositionTracker state (symbol → qty)
- Live run metadata (peak_equity, bar_count, trading_day, last_bq_ts)
- Intraday checkpoints for crash recovery
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

STATE_FILE = "state.json"
CHECKPOINT_FILE = "state_checkpoint.json"


class StateManager:
    """Serialise and restore multi-day live trading state."""

    def __init__(self, state_dir: str):
        self.dir = Path(state_dir)
        self.dir.mkdir(parents=True, exist_ok=True)

    # ── Public API ───────────────────────────────────────────────────

    @property
    def state_path(self) -> Path:
        return self.dir / STATE_FILE

    @property
    def checkpoint_path(self) -> Path:
        return self.dir / CHECKPOINT_FILE

    def exists(self) -> bool:
        """Check if a saved state exists from a previous session."""
        return self.state_path.exists()

    def checkpoint_exists(self) -> bool:
        """Check if an intraday checkpoint exists (crash recovery)."""
        return self.checkpoint_path.exists()

    def save(self, portfolio, position_tracker, live_state: dict):
        """Persist full state to disk (called at end of each trading day).

        Parameters
        ----------
        portfolio : Portfolio
            Engine portfolio with cash and positions.
        position_tracker : PositionTracker
            OMS position tracker.
        live_state : dict
            Runtime metadata (peak_equity, bar_count, trading_day, etc.).
        """
        data = {
            "version": 1,
            "saved_at": datetime.now(timezone.utc).isoformat(),
            "trading_day": live_state.get("trading_day", 0),
            "portfolio": self._serialise_portfolio(portfolio),
            "tracker": dict(position_tracker.positions) if position_tracker else {},
            "live_state": {
                "peak_equity": live_state.get("peak_equity", 0.0),
                "daily_start_equity": live_state.get("daily_start_equity", 0.0),
                "bar_count": live_state.get("bar_count", 0),
                "last_bq_ts": live_state.get("last_bq_ts", ""),
                "stop_reason": live_state.get("stop_reason"),
            },
        }
        try:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            self.state_path.write_text(
                json.dumps(data, indent=2, ensure_ascii=False)
            )
            logger.info("State saved: day=%d cash=%.2f positions=%d",
                        data["trading_day"], data["portfolio"]["cash"],
                        len(data["portfolio"]["positions"]))
        except Exception:
            logger.exception("Failed to save state")

    def load(self) -> dict:
        """Load state from disk.

        Returns dict with keys:
            portfolio_data, tracker_data, live_state, trading_day
        """
        if not self.exists():
            raise FileNotFoundError(f"No state file at {self.state_path}")

        data = json.loads(self.state_path.read_text())
        logger.info("State loaded: day=%d cash=%.2f positions=%d",
                    data.get("trading_day", 0),
                    data["portfolio"]["cash"],
                    len(data["portfolio"]["positions"]))
        return {
            "portfolio_data": data["portfolio"],
            "tracker_data": data.get("tracker", {}),
            "live_state": data.get("live_state", {}),
            "trading_day": data.get("trading_day", 0),
        }

    def save_checkpoint(self, portfolio, live_state: dict):
        """Save intraday checkpoint for crash recovery."""
        data = {
            "version": 1,
            "saved_at": datetime.now(timezone.utc).isoformat(),
            "trading_day": live_state.get("trading_day", 0),
            "portfolio": self._serialise_portfolio(portfolio),
            "live_state": {
                "peak_equity": live_state.get("peak_equity", 0.0),
                "daily_start_equity": live_state.get("daily_start_equity", 0.0),
                "bar_count": live_state.get("bar_count", 0),
                "last_bq_ts": live_state.get("last_bq_ts", ""),
            },
            "last_prices": getattr(portfolio, '_last_prices', {}),
        }
        try:
            self.checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
            self.checkpoint_path.write_text(
                json.dumps(data, indent=2, ensure_ascii=False)
            )
        except Exception:
            logger.exception("Failed to save checkpoint")

    def load_checkpoint(self) -> Optional[dict]:
        """Load intraday checkpoint if it exists and is newer than full state."""
        if not self.checkpoint_exists():
            return None

        cp_data = json.loads(self.checkpoint_path.read_text())
        cp_time = datetime.fromisoformat(cp_data["saved_at"])

        # Check if checkpoint is newer than full state
        if self.exists():
            state_data = json.loads(self.state_path.read_text())
            state_time = datetime.fromisoformat(state_data["saved_at"])
            if cp_time <= state_time:
                logger.info("Checkpoint is older than full state — ignoring")
                return None

        logger.info("Recovering from checkpoint (saved at %s)", cp_data["saved_at"])
        return {
            "portfolio_data": cp_data["portfolio"],
            "live_state": cp_data["live_state"],
            "trading_day": cp_data.get("trading_day", 0),
            "last_prices": cp_data.get("last_prices", {}),
        }

    def clear_checkpoint(self):
        """Remove checkpoint file (called after successful day-end save)."""
        try:
            if self.checkpoint_exists():
                self.checkpoint_path.unlink()
        except Exception:
            pass

    # ── Internals ────────────────────────────────────────────────────

    @staticmethod
    def _serialise_portfolio(portfolio) -> dict:
        """Convert Portfolio to JSON-serializable dict."""
        positions = {}
        for sym, pos in portfolio.positions.items():
            if pos.size == 0:
                continue
            positions[sym] = {
                "size": pos.size,
                "avg_entry": pos.avg_entry,
                "total_cost": getattr(pos, "_total_cost", pos.avg_entry * pos.size),
                "realized_pnl": pos.realized_pnl,
            }
        return {
            "cash": portfolio.cash,
            "initial_capital": portfolio.initial_capital,
            "positions": positions,
            "last_prices": getattr(portfolio, '_last_prices', {}),
        }

    @staticmethod
    def restore_portfolio(portfolio_data: dict, PortfolioClass, PositionClass):
        """Reconstruct a Portfolio from serialised data.

        Parameters
        ----------
        portfolio_data : dict
            Serialised portfolio from save/load.
        PortfolioClass : type
            The Portfolio class (to avoid circular imports).
        PositionClass : type
            The Position class.

        Returns
        -------
        Portfolio
        """
        portfolio = PortfolioClass(initial_capital=portfolio_data["initial_capital"])
        portfolio.cash = portfolio_data["cash"]

        for sym, pos_data in portfolio_data.get("positions", {}).items():
            pos = PositionClass(symbol=sym, entry_price=pos_data["avg_entry"])
            pos.size = pos_data["size"]
            pos._total_cost = pos_data.get("total_cost", pos_data["avg_entry"] * pos_data["size"])
            pos.realized_pnl = pos_data.get("realized_pnl", 0.0)
            portfolio.positions[sym] = pos

        if "last_prices" in portfolio_data:
            portfolio._last_prices = portfolio_data["last_prices"]
        return portfolio
