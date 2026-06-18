"""Unified JSON logging for quant pipeline.

Usage:
    from common.logging_util import get_logger

    log = get_logger("live.runner", env="dev", module="live",
                     log_file="/var/log/quant/dev/live/exp1.log")
    log.info("Day 1 starting...")
    # → {"ts":"2026-06-03T04:00:00.123Z","level":"INFO","logger":"live.runner",
    #    "quant_env":"dev","quant_module":"live","msg":"Day 1 starting..."}
"""

import json
import logging
import os
from datetime import datetime, timezone


class QuantJsonFormatter(logging.Formatter):
    """JSON-line formatter with env/module context fields."""

    def format(self, record: logging.LogRecord) -> str:
        now = datetime.now(timezone.utc)  # noqa: UP017
        ts = now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z"

        entry: dict = {
            "ts": ts,
            "level": record.levelname,
            "logger": record.name,
            "quant_env": getattr(record, "env", "unknown"),
            "quant_module": getattr(record, "module", "unknown"),
            "msg": record.getMessage(),
        }

        if record.exc_info and record.exc_info[1]:
            entry["exception"] = str(record.exc_info[1])
            entry["traceback"] = self.formatException(record.exc_info)

        return json.dumps(entry, ensure_ascii=False)


class _ContextFilter(logging.Filter):
    """Inject env/module into every LogRecord."""

    def __init__(self, env: str, module: str):
        super().__init__()
        self.env = env
        self.module = module

    def filter(self, record: logging.LogRecord) -> bool:
        record.env = self.env
        record.module = self.module
        return True


def get_logger(
    name: str,
    env: str,
    module: str,
    log_file: str,
    level: int = logging.INFO,
) -> logging.Logger:
    """Create a logger that writes JSON lines to a file.

    Args:
        name: Logger name (e.g. "live.runner").
        env: "prod" or "dev".
        module: Category (collector/loader/live/cron/factor/quality/train/backfill/adhoc).
        log_file: Absolute path to the log file.
        level: Logging level (default INFO).

    Returns:
        Configured logger.
    """
    logger = logging.getLogger(name)
    logger.setLevel(level)
    logger.propagate = False

    # Avoid duplicate handlers on repeated calls
    target = os.path.abspath(log_file)
    already_has = any(
        isinstance(h, logging.FileHandler) and os.path.abspath(h.baseFilename) == target
        for h in logger.handlers
    )
    if not already_has:
        os.makedirs(os.path.dirname(log_file), exist_ok=True)
        handler = logging.FileHandler(log_file)
        handler.setFormatter(QuantJsonFormatter())
        logger.addHandler(handler)

    # Attach context filter to handler (handler filters always apply, unlike logger filters)
    for f in list(handler.filters):
        if isinstance(f, _ContextFilter):
            handler.removeFilter(f)
    handler.addFilter(_ContextFilter(env, module))

    return logger
