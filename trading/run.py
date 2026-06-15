#!/usr/bin/env python3
"""Trading runner CLI — launched by the admin worker as a subprocess.

Usage:
    python3 trading/run.py --strategy-id 1 --env sim
"""

import argparse
import logging
import os
import sys
import signal
import time
from datetime import datetime, timezone
from pathlib import Path

# Ensure project root is on path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from common.logging_util import get_logger


def _setup_logging(strategy_id: int, env: str) -> logging.Logger:
    """Configure JSON logging to /var/log/quant/prod/trading_{env}/."""
    module = f"trading_{env}"
    log_dir = Path(f"/var/log/quant/prod/{module}")
    log_dir.mkdir(parents=True, exist_ok=True)

    log = get_logger(
        name=f"trading.runner.{strategy_id}",
        env="prod",
        module=module,
        log_file=str(log_dir / f"strategy_{strategy_id}.log"),
    )
    return log


def run_strategy(strategy_id: int, env: str) -> None:
    """Load a strategy from the trading DB and run it."""
    log = _setup_logging(strategy_id, env)

    # Import here so logging is configured first
    import yaml
    from trading import get_trading_session
    from trading.models import TradingStrategy as TSModel
    from trading.runner import TradingRunner
    from live.state import StateManager
    from trading.adapter import StrategyAdapter
    from trading.capital import CapitalManager
    from trading.signal_bridge import SignalBridge
    from trading.state import TradingStateManager

    log.info("Starting strategy #%d (%s)", strategy_id, env)

    # Load strategy from DB
    session = get_trading_session(env)
    try:
        strat = session.get(TSModel, strategy_id)
        if not strat:
            log.error("Strategy #%d not found", strategy_id)
            return
        if strat.status != "running":
            log.warning("Strategy #%d status is '%s', not 'running' — aborting", strategy_id, strat.status)
            return
    finally:
        session.close()

    # Parse config
    cfg = yaml.safe_load(strat.config_yaml) or {}
    market = strat.market or cfg.get("live", {}).get("market", "us")
    broker_cfg = cfg.get("broker", {})

    log.info(
        "Strategy: %s | market=%s | capital=$%.0f",
        strat.name, market, strat.capital_allocated,
    )

    # Initialize components
    from trading.capital import CapitalManager
    from trading.state import TradingStateManager
    from trading.signal_bridge import SignalBridge

    capital = CapitalManager(strat.capital_allocated)
    state_mgr = TradingStateManager(strategy_id, env)
    bridge = SignalBridge(strategy_id, env)
    broker = None  # Will be lazily created

    runner = TradingRunner(
        broker=broker,
        capital=capital,
        state=state_mgr,
        bridge=bridge,
        strategies=[strat],
        market=market,
    )

    # Write PID file for the admin worker to track
    pid_dir = Path(f"/var/quant/trading/{env}/pids")
    pid_dir.mkdir(parents=True, exist_ok=True)
    pid_file = pid_dir / f"strategy_{strategy_id}.pid"
    pid_file.write_text(str(os.getpid()))

    # Handle SIGTERM to clean up
    def _handle_sigterm(signum, frame):
        log.warning("Received SIGTERM — stopping strategy #%d", strategy_id)
        runner.stop()
        # Cleanup PID file
        if pid_file.exists():
            pid_file.unlink()
        sys.exit(0)

    signal.signal(signal.SIGTERM, _handle_sigterm)

    log.info("Starting trading loop...")
    try:
        runner.start_strategy(strat)

        # Keep alive — TradingRunner manages threads internally
        # This process lives until SIGTERM (from stop API)
        while True:
            time.sleep(10)
            # Check if the strategy is still supposed to be running
            session = get_trading_session(env)
            try:
                s = session.get(TSModel, strategy_id)
                if s and s.status != "running":
                    log.info("Strategy status changed to '%s', stopping...", s.status)
                    runner.stop()
                    break
            finally:
                session.close()

    except KeyboardInterrupt:
        log.info("Keyboard interrupt — stopping")
    except Exception:
        log.exception("Strategy #%d fatal error", strategy_id)
    finally:
        runner.stop()
        if pid_file.exists():
            pid_file.unlink()
        log.info("Strategy #%d stopped", strategy_id)


def main():
    parser = argparse.ArgumentParser(description="Run a trading strategy")
    parser.add_argument("--strategy-id", type=int, required=True, help="Strategy ID")
    parser.add_argument("--env", default="sim", choices=["sim", "real"], help="Environment")
    args = parser.parse_args()

    run_strategy(args.strategy_id, args.env)


if __name__ == "__main__":
    main()
