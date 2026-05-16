"""Alert manager for trading risk monitoring.

Fire alerts with severity levels. Handlers (console, log, future: Slack/email)
process alerts via the observer pattern.
"""

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable


@dataclass
class Alert:
    level: str  # "info", "warning", "critical"
    message: str
    context: dict = field(default_factory=dict)
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self):
        return {
            "level": self.level,
            "message": self.message,
            "context": self.context,
            "timestamp": self.timestamp.isoformat(),
        }


class AlertManager:
    def __init__(self, max_history: int = 500):
        self.alerts: list[Alert] = []
        self._handlers: list[Callable] = []
        self.max_history = max_history

    def fire(self, level: str, message: str, context: dict | None = None):
        alert = Alert(level=level, message=message, context=context or {})
        self.alerts.append(alert)
        if len(self.alerts) > self.max_history:
            self.alerts = self.alerts[-self.max_history:]
        for handler in self._handlers:
            try:
                handler(alert)
            except Exception:
                pass

    def on_alert(self, handler: Callable[[Alert], None]):
        self._handlers.append(handler)

    def recent(self, n: int = 20) -> list[Alert]:
        return list(reversed(self.alerts[-n:]))

    def count_by_level(self, level: str) -> int:
        return sum(1 for a in self.alerts if a.level == level)


class ConsoleHandler:
    """Print alerts to stdout with color-coded prefixes."""
    COLORS = {"info": "\033[34m", "warning": "\033[33m", "critical": "\033[31m"}
    RESET = "\033[0m"

    def __call__(self, alert: Alert):
        color = self.COLORS.get(alert.level, "")
        print(f"{color}[{alert.level.upper()}]{self.RESET} {alert.timestamp.isoformat()}: {alert.message}")


class LogHandler:
    """Write alerts as structured JSON lines. Compatible with Cloud Logging."""
    def __call__(self, alert: Alert):
        print(json.dumps({
            "severity": alert.level.upper(),
            "message": alert.message,
            "timestamp": alert.timestamp.isoformat(),
            **alert.context,
        }))
