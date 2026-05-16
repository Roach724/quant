from dataclasses import dataclass, field
from datetime import datetime, timezone
import uuid

VALID_STATES = {
    "PENDING", "SUBMITTED", "ACKNOWLEDGED",
    "PARTIAL_FILL", "FILLED", "CANCELLED", "REJECTED",
}


@dataclass
class TrackedOrder:
    internal_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    broker_id: str | None = None
    symbol: str = ""
    side: str = ""
    qty: float = 0.0
    filled_qty: float = 0.0
    state: str = "PENDING"
    avg_fill_price: float | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    strategy_name: str = ""
    signal_id: str | None = None

    def transition(self, new_state: str):
        if new_state not in VALID_STATES:
            raise ValueError(f"Invalid state: {new_state}")
        self.state = new_state
        self.updated_at = datetime.now(timezone.utc)
