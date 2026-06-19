from datetime import datetime, timezone
from typing import Callable


class DomainEvent:
    """Base class for all domain events."""

    def __init__(self, event_type: str, payload: dict, correlation_id: str | None = None):
        self.event_type = event_type
        self.payload = payload
        self.correlation_id = correlation_id
        self.occurred_at = datetime.now(timezone.utc)

    def __repr__(self):
        return f"<DomainEvent {self.event_type} correlation={self.correlation_id}>"


class EventBus:
    """Simple in-process event bus for domain events."""

    def __init__(self):
        self._handlers: dict[str, list[Callable]] = {}

    def subscribe(self, event_type: str, handler: Callable):
        self._handlers.setdefault(event_type, []).append(handler)

    def publish(self, event: DomainEvent):
        for handler in self._handlers.get(event.event_type, []):
            handler(event)

    def clear(self):
        self._handlers.clear()


# Module-level singleton
event_bus = EventBus()


# Event type constants
FUNDS_RESERVED = "FundsReserved"
TRANSFER_COMPLETED = "TransferCompleted"
TRANSFER_FAILED = "TransferFailed"
DEPOSIT_COMPLETED = "DepositCompleted"
DEPOSIT_INITIATED = "DepositInitiated"
