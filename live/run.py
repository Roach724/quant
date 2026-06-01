#!/usr/bin/env python3.12
"""Live Loop CLI entry point.

Usage:
    python -m live.run --config live/configs/paper_us.yaml
    python -m live.run --mode paper --config live/configs/paper_us.yaml
    python -m live.run --mode live --config live/configs/live_us.yaml
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

# Configure logging before any imports
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-5s [%(name)s] %(message)s",
    stream=sys.stderr,
)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from live.runner import LiveRunner


def main():
    parser = argparse.ArgumentParser(description="Quant Live Trading Loop")
    parser.add_argument("--config", "-c", type=str, required=True,
                        help="Path to YAML config file")
    parser.add_argument("--mode", "-m", type=str, choices=["paper", "live"],
                        help="Override config mode")
    args = parser.parse_args()

    runner = LiveRunner(args.config)
    if args.mode:
        runner.config["live"]["mode"] = args.mode
        runner.mode = args.mode

    runner.run()


if __name__ == "__main__":
    main()
