from __future__ import annotations

import time
from typing import Callable, Optional

from axo_vem.domain.compute.endpoint import Endpoint
from axo_vem.domain.compute.repository import EndpointRepository
from axo_vem.domain.errors import ConflictError
from axo_vem.domain.virtual_environment.fleet import RUNNING
from axo_vem.infrastructure.transport.zmq_command.endpoint_client import resolve_rpc_uri

REPORTED_RUNNING = "running"


class EndpointPicker:
    """Chooses which of this VE's endpoints a command goes to: a running
    one, most recently seen first -- never a sleeping, draining or failed
    one. The endpoints themselves forward a job elsewhere when the cluster
    places it on another endpoint, so any running member is a valid entry
    point. When nothing is running but endpoints sleep, it wakes one and
    waits for it (the first job after a quiet period pays the cold start)."""

    def __init__(
        self,
        endpoint_repository: EndpointRepository,
        fleet=None,
        waker: Optional[Callable[[str], Optional[str]]] = None,
        wake_timeout_s: float = 120.0,
        poll_s: float = 0.5,
        sleep_fn: Callable[[float], None] = time.sleep,
    ) -> None:
        self._endpoints = endpoint_repository
        self._fleet = fleet
        self._waker = waker
        self._wake_timeout_s = wake_timeout_s
        self._poll_s = poll_s
        self._sleep_fn = sleep_fn

    def _running(self) -> Optional[Endpoint]:
        managed = {e.endpoint_id: e.state for e in self._fleet.all()} if self._fleet is not None else {}
        for endpoint in self._endpoints.list_all():
            if endpoint.status != REPORTED_RUNNING or not endpoint.router_bind:
                continue
            # Endpoints the VEM manages must also be running by its own
            # account (not sleeping/draining); unmanaged ones (e.g. started
            # by hand) are trusted on their own report.
            if managed and managed.get(endpoint.endpoint_id, RUNNING) != RUNNING:
                continue
            return endpoint
        return None

    def pick(self) -> Endpoint:
        endpoint = self._running()
        if endpoint is not None:
            return endpoint
        if self._waker is not None and self._waker("a request arrived and no endpoint is running") is not None:
            waited = 0.0
            while waited < self._wake_timeout_s:
                self._sleep_fn(self._poll_s)
                waited += self._poll_s
                endpoint = self._running()
                if endpoint is not None:
                    return endpoint
            raise ConflictError(f"no endpoint woke up within {self._wake_timeout_s:g} s")
        raise ConflictError("no running endpoint in this virtual environment")

    def rpc_uri(self, endpoint: Endpoint) -> str:
        return resolve_rpc_uri(endpoint.router_bind, endpoint.endpoint_id)
