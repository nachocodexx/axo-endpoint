from __future__ import annotations

import json
import sqlite3
import threading
from abc import ABC, abstractmethod
from typing import Dict, Iterable, List

from option import Err, Ok, Result

from axo_shared.errors import JournalIOError
from axo_shared.messaging.models import OutboxMessage


class Journal(ABC):
    """The sender-side outbox: every message stays here until its ack arrives.

    Implementations must be safe to call from several threads at once --
    publishers append from whatever thread produced the event, while the
    ReliableSender's own thread reads, reschedules and acks.
    """

    @abstractmethod
    def append(self, message: OutboxMessage) -> Result[bool, JournalIOError]:
        """Store message. Idempotent on message_id: Ok(False) if it was already there."""

    @abstractmethod
    def due(self, now: float, limit: int) -> Result[List[OutboxMessage], JournalIOError]:
        """Up to ``limit`` messages whose next_attempt_at <= now, oldest due first."""

    @abstractmethod
    def reschedule(self, message_id: str, next_attempt_at: float) -> Result[None, JournalIOError]:
        """Record one more attempt and make the message due again at next_attempt_at.
        Not an error if the message was acked (removed) in the meantime."""

    @abstractmethod
    def ack(self, message_ids: Iterable[str]) -> Result[int, JournalIOError]:
        """Remove acked messages; returns how many were actually removed."""

    @abstractmethod
    def size(self) -> Result[int, JournalIOError]:
        """How many messages are still waiting for an ack."""

    def close(self) -> None:
        """Release any underlying resources."""


class InMemoryJournal(Journal):
    """Process-local journal for tests and dev -- lost on restart."""

    def __init__(self) -> None:
        self._messages: Dict[str, OutboxMessage] = {}
        self._lock = threading.Lock()

    def append(self, message: OutboxMessage) -> Result[bool, JournalIOError]:
        with self._lock:
            if message.message_id in self._messages:
                return Ok(False)
            self._messages[message.message_id] = message
            return Ok(True)

    def due(self, now: float, limit: int) -> Result[List[OutboxMessage], JournalIOError]:
        with self._lock:
            ready = [m for m in self._messages.values() if m.next_attempt_at <= now]
        ready.sort(key=lambda m: (m.next_attempt_at, m.created_at))
        return Ok(ready[:limit])

    def reschedule(self, message_id: str, next_attempt_at: float) -> Result[None, JournalIOError]:
        with self._lock:
            message = self._messages.get(message_id)
            if message is not None:
                self._messages[message_id] = message.rescheduled(next_attempt_at)
        return Ok(None)

    def ack(self, message_ids: Iterable[str]) -> Result[int, JournalIOError]:
        removed = 0
        with self._lock:
            for message_id in message_ids:
                if self._messages.pop(message_id, None) is not None:
                    removed += 1
        return Ok(removed)

    def size(self) -> Result[int, JournalIOError]:
        with self._lock:
            return Ok(len(self._messages))


_SCHEMA = """
CREATE TABLE IF NOT EXISTS outbox (
    message_id      TEXT PRIMARY KEY,
    event_type      TEXT NOT NULL,
    source_id       TEXT NOT NULL,
    data            TEXT NOT NULL,
    created_at      REAL NOT NULL,
    attempts        INTEGER NOT NULL,
    next_attempt_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS outbox_due ON outbox (next_attempt_at, created_at);
"""


class SqliteJournal(Journal):
    """Durable journal on a local SQLite file (stdlib only), so unacked usage
    records survive a process restart. WAL + synchronous=FULL: a message that
    append() returned Ok for is on disk even if the process dies right after.

    One connection, serialized through a lock -- the write volume is a handful
    of small rows per job, nowhere near SQLite's single-writer limit.
    """

    def __init__(self, path: str) -> None:
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=FULL")
        self._conn.executescript(_SCHEMA)

    def append(self, message: OutboxMessage) -> Result[bool, JournalIOError]:
        try:
            with self._lock:
                cursor = self._conn.execute(
                    "INSERT OR IGNORE INTO outbox VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        message.message_id,
                        message.event_type,
                        message.source_id,
                        json.dumps(message.data, default=str),
                        message.created_at,
                        message.attempts,
                        message.next_attempt_at,
                    ),
                )
            return Ok(cursor.rowcount == 1)
        except (sqlite3.Error, TypeError, ValueError) as exc:
            return Err(JournalIOError(str(exc), context={"message_id": message.message_id}))

    def due(self, now: float, limit: int) -> Result[List[OutboxMessage], JournalIOError]:
        try:
            with self._lock:
                rows = self._conn.execute(
                    "SELECT message_id, event_type, source_id, data, created_at, attempts, next_attempt_at "
                    "FROM outbox WHERE next_attempt_at <= ? ORDER BY next_attempt_at, created_at LIMIT ?",
                    (now, limit),
                ).fetchall()
        except sqlite3.Error as exc:
            return Err(JournalIOError(str(exc)))
        return Ok([
            OutboxMessage(
                message_id=row[0],
                event_type=row[1],
                source_id=row[2],
                data=json.loads(row[3]),
                created_at=row[4],
                attempts=row[5],
                next_attempt_at=row[6],
            )
            for row in rows
        ])

    def reschedule(self, message_id: str, next_attempt_at: float) -> Result[None, JournalIOError]:
        try:
            with self._lock:
                self._conn.execute(
                    "UPDATE outbox SET attempts = attempts + 1, next_attempt_at = ? WHERE message_id = ?",
                    (next_attempt_at, message_id),
                )
            return Ok(None)
        except sqlite3.Error as exc:
            return Err(JournalIOError(str(exc), context={"message_id": message_id}))

    def ack(self, message_ids: Iterable[str]) -> Result[int, JournalIOError]:
        ids = [(message_id,) for message_id in message_ids]
        if not ids:
            return Ok(0)
        try:
            with self._lock:
                before = self._conn.total_changes
                self._conn.executemany("DELETE FROM outbox WHERE message_id = ?", ids)
                return Ok(self._conn.total_changes - before)
        except sqlite3.Error as exc:
            return Err(JournalIOError(str(exc)))

    def size(self) -> Result[int, JournalIOError]:
        try:
            with self._lock:
                (count,) = self._conn.execute("SELECT COUNT(*) FROM outbox").fetchone()
            return Ok(count)
        except sqlite3.Error as exc:
            return Err(JournalIOError(str(exc)))

    def close(self) -> None:
        with self._lock:
            self._conn.close()
