from __future__ import annotations

import threading
from typing import Dict, List, Optional, Tuple

from axo_endpoint.core.errors import QueueWaitTimeoutError

WAIT_FOREVER = -1.0


class QueueDeadlines:
    """How long each job may wait for a free runner (its ``max_wait``).

    ``max_wait`` meanings (seconds):
    - ``-1``: wait indefinitely -- nothing is tracked;
    - ``0``: don't wait -- fail right away if the job would have to queue
      behind another job (a cold start is not a queue, so it still runs);
    - ``N``: wait up to N s counted from when the job was received, then
      fail with QueueWaitTimeoutError (never billed -- it never ran).

    Shared by both runtimes, which call admit() where a job enters a
    runner's queue and take() where it leaves it to actually run. App's
    sweep loop calls expired() so a job stuck behind a slow one is dropped
    on time instead of only when it finally reaches the front."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        # job_id -> (max_wait_s, deadline epoch)
        self._entries: Dict[str, Tuple[float, float]] = {}

    def set(self, job_id: str, max_wait_s: float, received_at: float) -> None:
        if max_wait_s < 0:
            return
        with self._lock:
            self._entries[job_id] = (max_wait_s, received_at + max_wait_s)

    def admit(self, job_id: str, runner_free: bool) -> Optional[QueueWaitTimeoutError]:
        """At enqueue time. Only ``max_wait=0`` can fail here: the job is
        rejected if its runner is busy, otherwise it is let through."""
        with self._lock:
            entry = self._entries.get(job_id)
            if entry is None or entry[0] > 0:
                return None
            self._entries.pop(job_id, None)
        if runner_free:
            return None
        return QueueWaitTimeoutError(
            "no free runner and max_wait is 0", context={"job_id": job_id, "max_wait_s": 0},
        )

    def take(self, job_id: str, now: float) -> Optional[QueueWaitTimeoutError]:
        """At dequeue time: stops tracking job_id and returns an error if it
        waited past its deadline (the caller then fails it instead of running it)."""
        with self._lock:
            entry = self._entries.pop(job_id, None)
        if entry is None or now <= entry[1]:
            return None
        return _timeout_error(job_id, entry[0])

    def pop_if_overdue(self, job_id: str, now: float) -> Optional[QueueWaitTimeoutError]:
        """Like take(), but keeps tracking job_id while it is still on time --
        for a job that keeps waiting (e.g. for room in the runner budget)."""
        with self._lock:
            entry = self._entries.get(job_id)
            if entry is None or now <= entry[1]:
                return None
            del self._entries[job_id]
        return _timeout_error(job_id, entry[0])

    def expired(self, now: float) -> List[Tuple[str, QueueWaitTimeoutError]]:
        """Jobs past their deadline that are still tracked (still waiting).
        They stay tracked: the caller drops them via the runtime, and a job
        that could not be dropped (e.g. still cold-starting) is caught by
        take() later."""
        with self._lock:
            late = [(job_id, max_wait) for job_id, (max_wait, deadline) in self._entries.items() if now > deadline]
        return [(job_id, _timeout_error(job_id, max_wait)) for job_id, max_wait in late]

    def discard(self, job_id: str) -> None:
        with self._lock:
            self._entries.pop(job_id, None)

    def __contains__(self, job_id: str) -> bool:
        with self._lock:
            return job_id in self._entries


def parse_max_wait(raw: object, default: float) -> Optional[float]:
    """A submitted ``max_wait`` (None = use ``default``) as seconds, or None
    if it is invalid: not a number, or negative other than -1."""
    if raw is None:
        return default
    if isinstance(raw, bool):
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    if value < 0 and value != WAIT_FOREVER:
        return None
    return value


def _timeout_error(job_id: str, max_wait_s: float) -> QueueWaitTimeoutError:
    return QueueWaitTimeoutError(
        f"job waited longer than max_wait ({max_wait_s:g}s) for a free runner",
        context={"job_id": job_id, "max_wait_s": max_wait_s},
    )
