from __future__ import annotations

import threading
from collections import OrderedDict
from typing import Optional

from option import Err, Ok, Result

from axo_shared.errors import MalformedAckError
from axo_shared.protocol import Command, CommandResult


def extract_message_id(command: Command) -> Optional[str]:
    """The reliable-channel id of an EVENT_PUBLISH command, or None if the
    sender was a fire-and-forget one (see events.envelope.encode_event)."""
    message_id = command.envelope.get("message_id")
    return message_id if isinstance(message_id, str) and message_id else None


def build_ack(message_id: str) -> CommandResult:
    """The reply a receiver sends once the message is durably stored -- only
    then, never before, or a crash in between would lose it."""
    return CommandResult(ok=True, metadata={"message_id": message_id})


def acked_message_id(result: CommandResult) -> Result[str, MalformedAckError]:
    """Reads the message_id back out of an ack built by build_ack."""
    message_id = result.metadata.get("message_id")
    if not result.ok or not isinstance(message_id, str) or not message_id:
        return Err(MalformedAckError(
            "reply is not an ack carrying a message_id",
            context={"ok": result.ok, "metadata_keys": sorted(result.metadata)},
        ))
    return Ok(message_id)


class RecentIds:
    """Bounded, in-process filter for redelivered messages (a resend whose
    first copy actually arrived but whose ack was lost).

    Only a fast first line: it forgets after ``maxlen`` ids and on restart.
    The durable guarantee comes from the store itself -- appending to Kurrent
    with the message_id as the event id, or a unique index on message_id in
    Mongo -- so a duplicate that slips past this filter is still dropped.
    """

    def __init__(self, maxlen: int = 10_000) -> None:
        self._maxlen = maxlen
        self._ids: "OrderedDict[str, None]" = OrderedDict()
        self._lock = threading.Lock()

    def seen(self, message_id: str) -> bool:
        """True if message_id was already recorded; records it otherwise."""
        with self._lock:
            if message_id in self._ids:
                self._ids.move_to_end(message_id)
                return True
            self._ids[message_id] = None
            if len(self._ids) > self._maxlen:
                self._ids.popitem(last=False)
            return False

    def forget(self, message_id: str) -> None:
        """Un-record an id, e.g. when storing the message failed after seen()
        was called, so the sender's resend is processed instead of skipped."""
        with self._lock:
            self._ids.pop(message_id, None)
