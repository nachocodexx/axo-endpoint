"""Job results on this endpoint: kept only for a while after the job ends.
The VEM keeps the result itself (in its VE's results bucket) as soon as it
hears the job finished; this copy only answers JOB_RESULT polls meanwhile,
so ``prune`` drops results finished more than ``keep_seconds`` ago -- the
store would otherwise grow with every job the endpoint ever ran."""

from __future__ import annotations

import threading
import time
from typing import Any, Callable, Dict

from option import Result

from axo_shared.storage.backend import StorageError, StorageKey
from axo_shared.storage.in_memory import InMemoryStorageBackend

PENDING = "PENDING"


class ResultStore(InMemoryStorageBackend):
    def __init__(self, now_fn: Callable[[], float] = time.time) -> None:
        super().__init__()
        self._now_fn = now_fn
        self._finished_at: Dict[str, float] = {}
        self._times = threading.Lock()

    def put(self, key: StorageKey, value: Any) -> Result[str, StorageError]:
        saved = super().put(key, value)
        with self._times:
            if _finished(value):
                self._finished_at[key.id] = self._now_fn()
            else:
                self._finished_at.pop(key.id, None)
        return saved

    def prune(self, keep_seconds: float) -> int:
        """Drops results finished more than ``keep_seconds`` ago; how many."""
        cutoff = self._now_fn() - keep_seconds
        with self._times:
            old = [job_id for job_id, at in self._finished_at.items() if at < cutoff]
            for job_id in old:
                del self._finished_at[job_id]
        for job_id in old:
            self.delete(StorageKey(id=job_id))
        return len(old)


def _finished(value: Any) -> bool:
    return not (getattr(value, "ok", True) is False and getattr(value, "error", None) == PENDING)
