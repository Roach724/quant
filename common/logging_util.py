"""Unified JSON logging for quant pipeline.

Usage:
    from common.logging_util import get_logger

    log = get_logger("live.runner", env="dev", module="live",
                     log_file="/var/log/quant/dev/live/exp1.log")
    log.info("Day 1 starting...")
    # → {"ts":"2026-06-03T04:00:00.123Z","severity":"INFO","logger":"live.runner",
    #    "quant_env":"dev","quant_module":"live","message":"Day 1 starting..."}
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
        record.env = self.env  # type: ignore[attr-defined]
        record.module = self.module  # type: ignore[attr-defined]
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


def setup_root_json(module: str, env: str = "prod", log_dir: str | None = None) -> None:
    """Add a JSON FileHandler to the root logger (one-liner for cron scripts).

    Keeps existing handlers intact. After calling this, ALL loggers that
    propagate to root will also write JSON to the file.

    Args:
        module: Category (loader/cron/factor/quality).
        env: "prod" or "dev".
        log_dir: Override log directory (default: /var/log/quant/{env}/{module}/).
    """
    import inspect

    if log_dir is None:
        log_dir = f"/var/log/quant/{env}/{module}"

    # Derive log file name from the calling script
    caller = inspect.stack()[1]
    script_name = os.path.splitext(os.path.basename(caller.filename))[0]
    # Sanitize: replace angle brackets and other problematic chars
    script_name = script_name.replace("<", "").replace(">", "").replace(" ", "_")
    if not script_name or script_name == "string":
        script_name = "unknown"
    log_file = os.path.join(log_dir, f"{script_name}.log")

    root = logging.getLogger()

    # Avoid duplicates
    target = os.path.abspath(log_file)
    for h in root.handlers:
        if isinstance(h, logging.FileHandler) and os.path.abspath(h.baseFilename) == target:
            return  # already configured

    os.makedirs(log_dir, exist_ok=True)
    handler = logging.FileHandler(log_file)
    handler.setFormatter(QuantJsonFormatter())
    handler.addFilter(_ContextFilter(env=env, module=module))
    root.addHandler(handler)
