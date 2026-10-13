from __future__ import annotations

import threading
import time
from typing import Callable, Dict, Generic, Hashable, Optional, Tuple, TypeVar

from axo_control_plane.domain.errors import UnavailableError

V = TypeVar("V")


class StaleOkCache(Generic[V]):
    """Answers from a slower source (Xolo), kept ``ttl_s`` seconds.

    - Fresh entry: used as is.
    - Expired entry: asked again; if the source can't answer
      (UnavailableError), the old answer is used (and ``stale_hits`` counts
      it) -- Xolo being down must not lock everyone out.
    - No entry and no answer: the UnavailableError goes to the caller, who
      refuses ("try again later"). Nothing is ever allowed by default.
    """

    def __init__(self, ttl_s: float, max_entries: int = 50_000, now_fn: Callable[[], float] = time.monotonic) -> None:
        self._ttl_s = ttl_s
        self._max_entries = max_entries
        self._now_fn = now_fn
        self._entries: Dict[Hashable, Tuple[float, V]] = {}
        self._lock = threading.Lock()
        self.stale_hits = 0

    def get(self, key: Hashable, load: Callable[[], V]) -> V:
        now = self._now_fn()
        with self._lock:
            entry = self._entries.get(key)
        if entry is not None and now - entry[0] < self._ttl_s:
            return entry[1]
        try:
            value = load()
        except UnavailableError:
            if entry is not None:
                with self._lock:
                    self.stale_hits += 1
                return entry[1]
            raise
        self.put(key, value)
        return value

    def peek(self, key: Hashable) -> Optional[V]:
        with self._lock:
            entry = self._entries.get(key)
        return entry[1] if entry is not None else None

    def put(self, key: Hashable, value: V) -> None:
        with self._lock:
            if len(self._entries) >= self._max_entries:
                # Drop the oldest tenth; cheap and good enough for a cache.
                for old in sorted(self._entries, key=lambda k: self._entries[k][0])[: self._max_entries // 10 or 1]:
                    del self._entries[old]
            self._entries[key] = (self._now_fn(), value)

    def drop(self, predicate: Callable[[Hashable], bool]) -> int:
        with self._lock:
            gone = [k for k in self._entries if predicate(k)]
            for k in gone:
                del self._entries[k]
        return len(gone)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
