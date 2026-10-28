from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timezone
from typing import Callable, List, Optional

from axo_vem.domain.events import models
from axo_vem.domain.events.publisher import EventPublisher
from axo_vem.domain.virtual_environment.fleet import FAILED, FORGOTTEN, REMOVED, Fleet, ManagedEndpoint, stream_name


class FleetService:
    """The endpoints this VEM manages. Every state change is an
    EndpointLifecycleChanged event in the VEM's own log (so the fleet
    survives restarts and every move has a reason), applied in memory too.
    Thread-safe."""

    def __init__(
        self,
        virtual_environment_id: str,
        event_publisher: EventPublisher,
        now_fn: Callable[[], float] = time.time,
    ) -> None:
        self._ve_id = virtual_environment_id
        self._now_fn = now_fn
        self._events = event_publisher
        self._fleet = Fleet()
        self._lock = threading.RLock()
        self._listeners: List[Callable[[ManagedEndpoint], None]] = []

    def on_change(self, listener: Callable[[ManagedEndpoint], None]) -> None:
        self._listeners.append(listener)

    def load(self, reader) -> int:
        try:
            records = reader.get_stream(stream_name(self._ve_id))
        except Exception:
            records = []
        replayed = 0
        with self._lock:
            for record in records or []:
                if record.type == models.ENDPOINT_LIFECYCLE_CHANGED:
                    self._fleet.apply(json.loads(record.data.decode("utf-8")))
                    replayed += 1
        return replayed

    def record(
        self,
        endpoint_id: str,
        state: str,
        reason: str,
        cpu_cores: Optional[float] = None,
        memory_mb: Optional[int] = None,
        failures: Optional[int] = None,
        replaces: Optional[str] = None,
    ) -> ManagedEndpoint:
        with self._lock:
            current = self._fleet.get(endpoint_id)
            event = models.EndpointLifecycleChanged(
                endpoint_id=endpoint_id, virtual_environment_id=self._ve_id, state=state, reason=reason,
                created_at=datetime.fromtimestamp(self._now_fn(), tz=timezone.utc),
                cpu_cores=cpu_cores if cpu_cores is not None else (current.cpu_cores if current else 1.0),
                memory_mb=memory_mb if memory_mb is not None else (current.memory_mb if current else 1024),
                failures=failures if failures is not None else (current.failures if current else 0),
                replaces=replaces if replaces is not None else (current.replaces if current else None),
            )
            data = event.model_dump(mode="json")
            self._events.append_to_stream(stream_name(self._ve_id), models.ENDPOINT_LIFECYCLE_CHANGED, data)
            endpoint = self._fleet.apply(data)
        for listener in self._listeners:
            listener(endpoint)
        return endpoint

    def forget(self, endpoint_id: str, by: Optional[str] = None) -> bool:
        """Drops a removed / failed endpoint from the fleet for good (its
        list entry). Anything else stays; False if there was nothing to drop."""
        with self._lock:
            current = self._fleet.get(endpoint_id)
            if current is None or current.state not in (REMOVED, FAILED):
                return False
            self.record(endpoint_id, FORGOTTEN, f"forgotten by {by}" if by else "forgotten")
            return True

    def is_forgotten(self, endpoint_id: str) -> bool:
        with self._lock:
            return self._fleet.is_forgotten(endpoint_id)

    def get(self, endpoint_id: str) -> Optional[ManagedEndpoint]:
        with self._lock:
            return self._fleet.get(endpoint_id)

    def all(self) -> List[ManagedEndpoint]:
        with self._lock:
            return self._fleet.all()

    def in_states(self, *states: str) -> List[ManagedEndpoint]:
        with self._lock:
            return self._fleet.in_states(states)
