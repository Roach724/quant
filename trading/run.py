#!/usr/bin/env python3
"""Trading runner CLI — launched by the admin worker as a subprocess.

Usage:
    python3 trading/run.py --strategy-id 1 --env sim
"""

import argparse
import logging
import os
import signal
import sys
import time
from pathlib import Path

# Ensure project root is on path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from common.logging_util import get_logger


def _parse_freq(freq_str: str) -> int:
    """Parse frequency string like '5m', '1h', '1d' to minutes."""
    freq_str = freq_str.strip().lower()
    if freq_str.endswith("m") and not freq_str.endswith("hm"):
        return int(freq_str[:-1])
    if freq_str.endswith("h"):
        return int(freq_str[:-1]) * 60
    if freq_str.endswith("d"):
        return int(freq_str[:-1]) * 1440
    raise ValueError(f"Unsupported freq format: {freq_str}")


def _setup_logging(strategy_id: int, strategy_name: str, env: str) -> logging.Logger:
    """Configure JSON logging to /var/log/quant/prod/trading_{env}/."""
    module = f"trading_{env}"
    log_dir = Path(f"/var/log/quant/prod/{module}")
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = str(log_dir / f"{strategy_name}.log")

    log = get_logger(
        name=f"trading.runner.{strategy_id}",
        env="prod",
        module=module,
        log_file=log_file,
    )

    # Wire all trading.* sub-module loggers to the strategy log file
    trading_logger = logging.getLogger("trading")
    for h in log.handlers:
        if not any(isinstance(eh, type(h)) for eh in trading_logger.handlers):
            trading_logger.addHandler(h)
    trading_logger.setLevel(log.level)

    # Also wire live.* (BQDataSource, MarketCalendar) to the strategy log
    live_logger = logging.getLogger("live")
    for h in log.handlers:
        if not any(isinstance(eh, type(h)) for eh in live_logger.handlers):
            live_logger.addHandler(h)
    live_logger.setLevel(log.level)

    # Wire strategies.* logger (MLPrediction, MACrossover, etc.)
    strategies_logger = logging.getLogger("strategies")
    for h in log.handlers:
        if not any(isinstance(eh, type(h)) for eh in strategies_logger.handlers):
            strategies_logger.addHandler(h)
    strategies_logger.setLevel(log.level)

    return log


def run_strategy(strategy_id: int, env: str) -> None:
    """Load a strategy from the trading DB and run it."""
    import yaml

    from trading import get_trading_session
    from trading.capital import CapitalManager
    from trading.models import TradingStrategy as TSModel
    from trading.runner import TradingRunner
    from trading.signal_bridge import SignalBridge
    from trading.state import TradingStateManager

    # Load strategy from DB first to get its name for log file naming
    session = get_trading_session(env)
    strat = session.get(TSModel, strategy_id)
    if not strat:
        print(f"Strategy #{strategy_id} not found in trading DB", flush=True)
        session.close()
        return
    if strat.status != "running":
        print(f"Strategy #{strategy_id} status is '{strat.status}', not 'running' — aborting", flush=True)
        session.close()
        return

    # Setup logging with strategy name AFTER we know it
    log = _setup_logging(strategy_id, strat.name, env)
    log.info("Starting strategy #%d (%s) — %s", strategy_id, strat.name, env)

    # Parse config
    cfg = yaml.safe_load(strat.config_yaml) or {}
    market = strat.market or cfg.get("live", {}).get("market", "us")
    live_cfg = cfg.get("live", {}) or {}
    _broker_cfg = cfg.get("broker", {})

    log.info(
        "Strategy: %s | market=%s | capital=$%.0f",
        strat.name,
        market,
        strat.capital_allocated,
    )

    # Parse scheduler config
    freq_str = live_cfg.get("freq", "5m")
    lookback_bars = live_cfg.get("lookback_bars", 0)
    freq_minutes = _parse_freq(freq_str)
    log.info(
        "Scheduler config: freq=%s (%dmin) lookback=%d bars rebalance_every=%d",
        freq_str, freq_minutes, lookback_bars,
        live_cfg.get("rebalance_every", 1),
    )

    # Initialize components with the trading DB session
    broker = None  # Will be lazily created in TradingRunner
    capital = CapitalManager(session)
    state_mgr = TradingStateManager(session)
    bridge = SignalBridge(broker, capital, market=market)

    # Create scheduler if lookback configured
    from trading.scheduler import RebalanceScheduler
    scheduler = None
    if lookback_bars > 0:
        state_dir = Path(f"/var/data/trading/{env}/state")
        scheduler_path = state_dir / f"strategy_{strategy_id}_scheduler.json"
        # Fresh start: clear old scheduler state from previous deployments.
        # Scheduler state is per-run; multi-day resume is handled by
        # TradingStateManager, not by the scheduler's own file.
        if scheduler_path.exists():
            scheduler_path.unlink()
            log.info("Cleared old scheduler state for fresh start")
        scheduler = RebalanceScheduler(
            freq_minutes=freq_minutes,
            lookback_bars=lookback_bars,
            rebalance_every=int(live_cfg.get("rebalance_every", 1)),
            state_path=str(scheduler_path),
        )
        log.info("Scheduler config: freq=%dm lookback=%d bars rebalance_every=%d",
                 freq_minutes, lookback_bars, int(live_cfg.get("rebalance_every", 1)))

    runner = TradingRunner(
        broker=broker,
        capital=capital,
        state=state_mgr,
        bridge=bridge,
        strategies=[strat],
        market=market,
        scheduler=scheduler,
    )

    # Write PID file for the admin worker to track
    pid_dir = Path(f"/var/data/trading/{env}/pids")
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
