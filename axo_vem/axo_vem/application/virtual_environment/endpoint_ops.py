from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional

from option import Result


class EndpointOps(ABC):
    """What the reconciler needs to do to endpoint containers and to ask the
    endpoints themselves -- a port, so the lifecycle logic is testable
    without Docker. Errors come back as plain messages (they end up as the
    reason on an EndpointLifecycleChanged event)."""

    @abstractmethod
    def create(self, cpu_cores: float, memory_mb: int) -> Result[str, str]:
        """Launches a new endpoint of this size; Ok(endpoint_id)."""

    @abstractmethod
    def container_state(self, endpoint_id: str) -> Optional[str]:
        """Docker's status ("running", "exited", ...) or None if it's gone."""

    @abstractmethod
    def reported(self, endpoint_id: str) -> Optional[tuple]:
        """What the endpoint itself last reported: (status, last_seen_epoch),
        or None if it never reported."""

    @abstractmethod
    def halt(self, endpoint_id: str) -> Optional[str]:
        """Stops the container, keeping it (sleep). None on success, else why not."""

    @abstractmethod
    def start(self, endpoint_id: str) -> Optional[str]:
        """Starts a halted container (wake / VE start)."""

    @abstractmethod
    def restart(self, endpoint_id: str) -> Optional[str]: ...

    @abstractmethod
    def remove(self, endpoint_id: str) -> Optional[str]:
        """Stops and deletes the container for good. Already gone = success."""

    @abstractmethod
    def cancel_job(self, endpoint_id: str, job_id: str) -> None:
        """Best effort JOB_CANCEL (a VE stop whose drain timed out)."""

    @abstractmethod
    def drain(self, endpoint_id: str, draining: bool = True) -> Optional[int]:
        """Tells the endpoint to take no new jobs (ENDPOINT_DRAIN); returns
        how many jobs it still runs, or None if it didn't answer."""
