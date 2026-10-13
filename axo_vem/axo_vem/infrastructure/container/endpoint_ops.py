from __future__ import annotations

from datetime import datetime
from typing import Optional

from option import Err, Ok, Result

from axo_shared import wire
from axo_shared.container.spawner import ContainerSpawner
from axo_shared.protocol import Command
from axo_shared.runtime.spec import ContainerMode

from axo_vem.application.nodes.launch_endpoint_node import LaunchEndpointNodeUseCase
from axo_vem.application.virtual_environment.endpoint_ops import EndpointOps
from axo_vem.domain.compute.repository import EndpointRepository
from axo_vem.infrastructure.transport.zmq_command.endpoint_client import resolve_rpc_uri, send_command


class DockerEndpointOps(EndpointOps):
    """EndpointOps over Docker/Swarm (ContainerSpawner -- endpoint containers
    are named exactly their endpoint_id) plus the endpoints' own reports
    (the endpoints read model) and ZMQ commands to them."""

    def __init__(
        self,
        spawner: ContainerSpawner,
        mode: ContainerMode,
        launcher: LaunchEndpointNodeUseCase,
        api_uri: str,
        endpoint_repository: EndpointRepository,
        command_timeout_seconds: float = 3.0,
    ) -> None:
        self._spawner = spawner
        self._mode = mode
        self._launcher = launcher
        self._api_uri = api_uri
        self._endpoints = endpoint_repository
        self._timeout = command_timeout_seconds

    def create(self, cpu_cores: float, memory_mb: int) -> Result[str, str]:
        launched = self._launcher.execute(api_uri=self._api_uri, cpu_cores=cpu_cores, memory_mb=memory_mb)
        if launched.is_err:
            return Err(str(launched.unwrap_err()))
        return Ok(launched.unwrap().endpoint_id)

    def container_state(self, endpoint_id: str) -> Optional[str]:
        return self._spawner.state(self._mode, endpoint_id)

    def reported(self, endpoint_id: str) -> Optional[tuple]:
        endpoint = self._endpoints.get(endpoint_id)
        if endpoint is None or endpoint.status is None:
            return None
        try:
            last_seen = (
                datetime.fromisoformat(endpoint.last_seen_at.replace("Z", "+00:00")).timestamp()
                if endpoint.last_seen_at else 0.0
            )
        except ValueError:
            last_seen = 0.0
        return endpoint.status, last_seen

    def _handle(self, endpoint_id: str):
        return self._spawner.get(self._mode, endpoint_id)

    def _act(self, endpoint_id: str, action) -> Optional[str]:
        handle = self._handle(endpoint_id)
        if handle is None:
            return f"no container named {endpoint_id}"
        result = action(handle)
        return None if result.is_ok else str(result.unwrap_err())

    def halt(self, endpoint_id: str) -> Optional[str]:
        return self._act(endpoint_id, lambda h: self._spawner.halt(h, timeout=10))

    def start(self, endpoint_id: str) -> Optional[str]:
        return self._act(endpoint_id, self._spawner.start)

    def restart(self, endpoint_id: str) -> Optional[str]:
        return self._act(endpoint_id, self._spawner.restart)

    def remove(self, endpoint_id: str) -> Optional[str]:
        handle = self._handle(endpoint_id)
        if handle is None:
            return None  # already gone
        result = self._spawner.stop(handle, timeout=10)
        return None if result.is_ok else str(result.unwrap_err())

    def cancel_job(self, endpoint_id: str, job_id: str) -> None:
        endpoint = self._endpoints.get(endpoint_id)
        if endpoint is None or not endpoint.router_bind:
            return
        command = Command(operation=wire.JOB_CANCEL, content_type="application/json", envelope={"job_id": job_id})
        send_command(resolve_rpc_uri(endpoint.router_bind, endpoint_id), command, self._timeout)

    def drain(self, endpoint_id: str, draining: bool = True) -> Optional[int]:
        endpoint = self._endpoints.get(endpoint_id)
        if endpoint is None or not endpoint.router_bind:
            return None
        command = Command(
            operation=wire.ENDPOINT_DRAIN, content_type="application/json", envelope={"draining": draining},
        )
        result = send_command(resolve_rpc_uri(endpoint.router_bind, endpoint_id), command, self._timeout)
        if result.is_err or not result.unwrap().ok:
            return None
        return int((result.unwrap().metadata or {}).get("active_jobs", 0))
