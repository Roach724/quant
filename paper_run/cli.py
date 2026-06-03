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
