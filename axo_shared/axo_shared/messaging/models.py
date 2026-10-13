from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, Dict


@dataclass(frozen=True)
class OutboxMessage:
    """One event waiting in a sender's journal until the receiver acks it.

    ``message_id`` is the unique id the receiver echoes back in its ack and
    uses to discard duplicates -- by convention the taxonomy event's own
    ``event_id`` (see axo_shared.events.models.EventEnvelope), so the same id
    follows the event all the way into Kurrent/Mongo.
    """

    message_id: str
    event_type: str
    source_id: str
    data: Dict[str, Any] = field(default_factory=dict)
    created_at: float = 0.0
    attempts: int = 0
    next_attempt_at: float = 0.0

    def rescheduled(self, next_attempt_at: float) -> "OutboxMessage":
        """A copy with one more attempt recorded, due again at next_attempt_at."""
        return replace(self, attempts=self.attempts + 1, next_attempt_at=next_attempt_at)
