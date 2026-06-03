#!/usr/bin/env python3.12
"""Live Loop CLI entry point.

Usage:
    python -m live.run --config live/configs/paper_us.yaml
    python -m live.run --mode paper --config live/configs/paper_us.yaml
    python -m live.run --mode live --config live/configs/live_us.yaml

Logging:
    JSON lines → /var/log/quant/dev/live/{experiment_id}.log
    Plain text  → stderr (dev visibility)
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from live.config import load_config
from live.runner import LiveRunner


def main():
    parser = argparse.ArgumentParser(description="Quant Live Trading Loop")
    parser.add_argument("--config", "-c", type=str, required=True,
                        help="Path to YAML config file")
    parser.add_argument("--mode", "-m", type=str, choices=["paper", "live"],
                        help="Override config mode")
    args = parser.parse_args()

    # Load config early to determine log file path
    config = load_config(args.config)
    exp_id = config.get("experiment", {}).get("id", Path(args.config).stem)
    log_file = f"/var/log/quant/dev/live/{exp_id}.log"

    # Set up root logger: JSON to file + plain text to stderr
    from common.logging_util import QuantJsonFormatter, _ContextFilter

    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.handlers.clear()

    file_handler = logging.FileHandler(log_file)
    file_handler.setFormatter(QuantJsonFormatter())
    root.addHandler(file_handler)

    stderr_handler = logging.StreamHandler(sys.stderr)
    stderr_handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)-5s [%(name)s] %(message)s")
    )
    root.addHandler(stderr_handler)

    # Attach context filter to handlers (logger filters don't propagate to children)
    ctx = _ContextFilter(env="dev", module="live")
    file_handler.addFilter(ctx)
    stderr_handler.addFilter(ctx)

    logger = logging.getLogger(__name__)
    logger.info("Logging to %s (exp=%s)", log_file, exp_id)

    runner = LiveRunner(args.config, config=config)
    if args.mode:
        runner.config["live"]["mode"] = args.mode
        runner.mode = args.mode

    runner.run()


if __name__ == "__main__":
    main()
