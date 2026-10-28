from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Dict, Iterable, List, Optional, Set

CREATING = "creating"    # container launched, endpoint not reporting yet
STARTING = "starting"    # restarted / woken, endpoint not reporting yet
RUNNING = "running"
SLEEPING = "sleeping"    # idle: container stopped on purpose, kept for a fast wake
DRAINING = "draining"    # scale-down / stop: finishing its jobs, takes no new ones
REMOVING = "removing"
REMOVED = "removed"
STOPPED = "stopped"      # the VE is stopped: container kept for Start
FAILED = "failed"        # died unexpectedly
FORGOTTEN = "forgotten"  # a removed / failed one the user cleared from the list: dropped from the fleet

# Count toward target_endpoints: they exist, or will in a moment.
PRESENT = frozenset({CREATING, STARTING, RUNNING, SLEEPING})
# Count toward health: the VE is using them on purpose.
PLANNED_DOWN = frozenset({SLEEPING, DRAINING, REMOVING, REMOVED, STOPPED})


def stream_name(virtual_environment_id: str) -> str:
    return f"ve-fleet-{virtual_environment_id}"


@dataclass(frozen=True)
class ManagedEndpoint:
    """One endpoint the VEM created and looks after."""

    endpoint_id: str
    state: str
    cpu_cores: float
    memory_mb: int
    since: str               # ISO time of the last state change
    failures: int = 0        # consecutive failures of this slot
    replaces: Optional[str] = None
    reason: Optional[str] = None


class Fleet:
    """Every endpoint of this VE as the VEM knows it, rebuilt from
    EndpointLifecycleChanged events (last event per endpoint wins)."""

    def __init__(self) -> None:
        self._endpoints: Dict[str, ManagedEndpoint] = {}
        self._forgotten: Set[str] = set()   # never listed again

    def apply(self, data: Dict) -> ManagedEndpoint:
        """The endpoint after this event (a ``forgotten`` one leaves the fleet)."""
        current = self._endpoints.get(data["endpoint_id"])
        endpoint = ManagedEndpoint(
            endpoint_id=data["endpoint_id"], state=data["state"],
            cpu_cores=float(data.get("cpu_cores", current.cpu_cores if current else 1.0)),
            memory_mb=int(data.get("memory_mb", current.memory_mb if current else 1024)),
            since=str(data.get("created_at", "")), failures=int(data.get("failures", 0)),
            replaces=data.get("replaces"), reason=data.get("reason"),
        )
        if endpoint.state == FORGOTTEN:
            self._endpoints.pop(endpoint.endpoint_id, None)
            self._forgotten.add(endpoint.endpoint_id)
        else:
            self._endpoints[endpoint.endpoint_id] = endpoint
        return endpoint

    def get(self, endpoint_id: str) -> Optional[ManagedEndpoint]:
        return self._endpoints.get(endpoint_id)

    def is_forgotten(self, endpoint_id: str) -> bool:
        return endpoint_id in self._forgotten

    def all(self) -> List[ManagedEndpoint]:
        return sorted(self._endpoints.values(), key=lambda e: (e.since, e.endpoint_id))

    def in_states(self, states: Iterable[str]) -> List[ManagedEndpoint]:
        wanted = set(states)
        return [e for e in self.all() if e.state in wanted]

    def count(self, states: Iterable[str]) -> int:
        return len(self.in_states(states))
