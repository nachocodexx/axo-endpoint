from __future__ import annotations

import random
import threading
import time
from typing import Any, Callable, Dict, Optional
from uuid import uuid4

from option import Result

from axo_shared.errors import AxoError, JournalIOError
from axo_shared.messaging.journal import Journal
from axo_shared.messaging.models import OutboxMessage
from axo_shared.messaging.transport import AckTransport

OnDeliveryRetry = Callable[[OutboxMessage, int, Optional[AxoError]], None]


def backoff_delay(
    attempt: int,
    base_delay_s: float,
    max_delay_s: float,
    rng: Callable[[], float] = random.random,
) -> float:
    """Exponential backoff with "equal jitter": half the capped delay is fixed,
    the other half random, so many senders recovering at once don't resend in
    lockstep. ``attempt`` counts from 1."""
    capped = min(max_delay_s, base_delay_s * (2 ** max(0, attempt - 1)))
    return capped / 2 + rng() * capped / 2


class ReliableSender:
    """Delivers events at-least-once: every published message stays in the
    journal until the receiver acks its message_id, and is resent on a backoff
    schedule until then. Pair it with receiver-side dedup (messaging.receiver)
    for effectively-once storage.

    publish() only writes to the journal -- it never touches the network, so it
    never blocks or fails because the receiver is slow or down. A single
    background thread owns the transport: it sends what's due, collects acks,
    and schedules the next attempt for everything still unacked.

    Shared code does no catalog logging: callers read stats() and pass
    on_delivery_retry to log through their own catalog.
    """

    def __init__(
        self,
        journal: Journal,
        transport: AckTransport,
        source_id: str,
        *,
        base_delay_s: float = 0.5,
        max_delay_s: float = 30.0,
        ack_timeout_s: float = 5.0,
        batch_size: int = 100,
        poll_interval_s: float = 0.1,
        on_delivery_retry: Optional[OnDeliveryRetry] = None,
        clock: Callable[[], float] = time.time,
        rng: Callable[[], float] = random.random,
    ) -> None:
        self._journal = journal
        self._transport = transport
        self._source_id = source_id
        self._base_delay_s = base_delay_s
        self._max_delay_s = max_delay_s
        self._ack_timeout_s = ack_timeout_s
        self._batch_size = batch_size
        self._poll_interval_s = poll_interval_s
        self._on_delivery_retry = on_delivery_retry
        self._clock = clock
        self._rng = rng

        self._stats_lock = threading.Lock()
        self._sent = 0
        self._acked = 0
        self._retries = 0
        self._send_failures = 0

        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def publish(
        self,
        event_type: str,
        data: Dict[str, Any],
        message_id: Optional[str] = None,
    ) -> Result[bool, JournalIOError]:
        """Queue one event for delivery. message_id defaults to the event's own
        ``event_id`` (every taxonomy event has one), else a fresh uuid4.
        Ok(False) means the same message_id was already queued."""
        now = self._clock()
        message = OutboxMessage(
            message_id=message_id or str(data.get("event_id") or uuid4()),
            event_type=event_type,
            source_id=self._source_id,
            data=data,
            created_at=now,
            attempts=0,
            next_attempt_at=now,
        )
        return self._journal.append(message)

    def run_once(self, poll_timeout_s: Optional[float] = None) -> None:
        """One send/ack cycle. The background thread just calls this in a loop;
        tests call it directly for deterministic stepping."""
        now = self._clock()
        due = self._journal.due(now, self._batch_size)
        for message in due.unwrap_or([]):
            self._attempt(message, now)

        acked = self._transport.poll_acks(self._poll_interval_s if poll_timeout_s is None else poll_timeout_s)
        if acked:
            removed = self._journal.ack(acked).unwrap_or(0)
            with self._stats_lock:
                self._acked += removed

    def _attempt(self, message: OutboxMessage, now: float) -> None:
        attempt = message.attempts + 1
        if message.attempts > 0:
            with self._stats_lock:
                self._retries += 1
        result = self._transport.send(message)
        if result.is_ok:
            with self._stats_lock:
                self._sent += 1
            # Due again only if no ack shows up within the ack timeout (+ backoff).
            next_at = now + self._ack_timeout_s + backoff_delay(attempt, self._base_delay_s, self._max_delay_s, self._rng)
            error: Optional[AxoError] = None
        else:
            with self._stats_lock:
                self._send_failures += 1
            next_at = now + backoff_delay(attempt, self._base_delay_s, self._max_delay_s, self._rng)
            error = result.unwrap_err()
        self._journal.reschedule(message.message_id, next_at)
        if message.attempts > 0 or error is not None:
            if self._on_delivery_retry is not None:
                self._on_delivery_retry(message, attempt, error)

    def stats(self) -> Dict[str, int]:
        """Counters for the queue metrics: pending is what's still unacked."""
        with self._stats_lock:
            counters = {
                "sent": self._sent,
                "acked": self._acked,
                "retries": self._retries,
                "send_failures": self._send_failures,
            }
        counters["pending"] = self._journal.size().unwrap_or(-1)
        return counters

    def start(self) -> None:
        """Start the background delivery thread (idempotent)."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name=f"reliable-sender-{self._source_id}", daemon=True)
        self._thread.start()

    def _loop(self) -> None:
        while not self._stop.is_set():
            self.run_once()

    def stop(self, flush_timeout_s: float = 2.0) -> None:
        """Give pending messages up to flush_timeout_s to be acked, then stop.
        Anything still unacked stays in the journal for the next start()."""
        deadline = time.monotonic() + flush_timeout_s
        while time.monotonic() < deadline and self._journal.size().unwrap_or(0) > 0:
            if self._thread is None or not self._thread.is_alive():
                break
            time.sleep(0.05)
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=max(1.0, self._poll_interval_s * 5))
            self._thread = None
        self._transport.close()
