from __future__ import annotations

import os
from typing import Any, Dict, Optional, Union

import zmq

from axo_shared.errors import AxoError
from axo_shared.messaging import InMemoryJournal, Journal, OutboxMessage, ReliableSender, SqliteJournal, ZmqDealerTransport
from axo_endpoint.log import DumbLogger, Log
from axo_endpoint.log.catalog import Component, Event

_Logger = Union[Log, DumbLogger]


def open_journal(outbox_path: str, endpoint_id: str) -> Journal:
    """":memory:" -> InMemoryJournal; anything else is a directory holding one
    SQLite journal per endpoint id (created if missing)."""
    if outbox_path == ":memory:":
        return InMemoryJournal()
    os.makedirs(outbox_path, exist_ok=True)
    return SqliteJournal(os.path.join(outbox_path, f"{endpoint_id}.db"))


class ReliableEventPublisher:
    """Drop-in replacement for the old fire-and-forget ZmqEventPublisher (same
    publish(event_type, data) / close() surface, so ExternalEventForwardingBridge
    and every other caller are unchanged), backed by axo_shared.messaging:

    - publish() only appends to a local journal -- it never blocks the caller
      (often a bus subscriber on a hot path) and never fails because the VEM
      is slow or down;
    - a background ReliableSender resends every event until the VEM acks its
      message_id (the event's own event_id), so nothing is lost -- which is
      what usage records need for billing.
    """

    def __init__(
        self,
        api_uri: str,
        endpoint_id: str,
        outbox_path: str = ":memory:",
        ack_timeout_s: float = 5.0,
        retry_base_s: float = 0.5,
        retry_max_s: float = 30.0,
        context: Optional["zmq.Context"] = None,
        logger: _Logger = None,
        journal: Optional[Journal] = None,
    ) -> None:
        self._logger: _Logger = logger or DumbLogger()
        self._sender = ReliableSender(
            journal if journal is not None else open_journal(outbox_path, endpoint_id),
            ZmqDealerTransport(api_uri, context=context),
            source_id=endpoint_id,
            base_delay_s=retry_base_s,
            max_delay_s=retry_max_s,
            ack_timeout_s=ack_timeout_s,
            on_delivery_retry=self._on_delivery_retry,
        )
        self._sender.start()

    def publish(self, event_type: str, data: Dict[str, Any]) -> None:
        """Queues one event for delivery. Never blocks on the network or raises."""
        result = self._sender.publish(event_type, data)
        if result.is_err:
            self._logger.error_event(
                Event.External.PUBLISH_FAILED,
                component=Component.EXTERNAL_FORWARDING,
                event_type=event_type,
                **result.unwrap_err().to_dict(),
            )
            return
        self._logger.debug_event(
            Event.External.PUBLISHED,
            component=Component.EXTERNAL_FORWARDING,
            event_type=event_type,
        )

    def _on_delivery_retry(self, message: OutboxMessage, attempt: int, error: Optional[AxoError]) -> None:
        self._logger.warning_event(
            Event.External.DELIVERY_RETRY,
            component=Component.EXTERNAL_FORWARDING,
            event_type=message.event_type,
            message_id=message.message_id,
            attempt=attempt,
            error=error.message if error is not None else "ack timeout",
        )

    def stats(self) -> Dict[str, int]:
        """pending / sent / acked / retries / send_failures -- for metrics."""
        return self._sender.stats()

    def close(self, flush_timeout_s: float = 2.0) -> None:
        """Gives pending events a moment to be acked (e.g. EndpointStopped, the
        last event ever sent), then stops. Anything still unacked stays in the
        journal and is resent on the next start."""
        self._sender.stop(flush_timeout_s=flush_timeout_s)
