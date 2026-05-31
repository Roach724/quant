"""Live Loop configuration loader and validation."""
from __future__ import annotations

import yaml
from pathlib import Path

DEFAULT_VALUES = {
    "broker": {
        "paper": {"initial_capital": 100000, "slippage_bps": 5, "commission_bps": 1, "min_commission": 1.0},
        "live": {"type": "futu_stock", "host": "127.0.0.1", "port": 11111, "max_position_pct": 0.2},
    },
    "schedule": {"pre_market_warmup": 300, "bar_interval": 300, "market_close_offset": 600},
    "risk": {"max_drawdown": 0.15, "max_daily_loss": 0.05, "position_size_pct": 0.2},
    "observer": {"log_dir": "output/live/", "snapshot_interval": 60, "trade_log": True, "equity_curve": True},
    "dashboard": {"port": 8090, "websocket": True},
}


def load_config(path: str) -> dict:
    """Load and validate a YAML config file, applying defaults for missing keys."""
    with open(path) as f:
        config = yaml.safe_load(f)
    _apply_defaults(config, DEFAULT_VALUES)
    mode = config.get("live", {}).get("mode", "paper")
    if mode not in ("paper", "live"):
        raise ValueError(f"live.mode must be 'paper' or 'live', got '{mode}'")
    market = config.get("live", {}).get("market", "us")
    if market not in ("us", "hk", "crypto"):
        raise ValueError(f"live.market must be us/hk/crypto, got '{market}'")
    from datetime import datetime, timezone
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    subdir = f"{market}_{mode}_{ts}"
    output_root = config.get("live", {}).get("output_dir", "output/live/")
    config["_output_dir"] = str(Path(output_root) / subdir)
    return config


def _apply_defaults(config: dict, defaults: dict):
    for key, value in defaults.items():
        if key not in config:
            config[key] = value
        elif isinstance(value, dict) and isinstance(config.get(key), dict):
            _apply_defaults(config[key], value)
