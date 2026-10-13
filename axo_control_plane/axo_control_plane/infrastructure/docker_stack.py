from __future__ import annotations

import io
from typing import Dict, List, Optional

import docker
import docker.errors

from axo_control_plane.application.ports import ContainerInfo, ContainerRun, StackDriver
from axo_control_plane.domain.errors import UnavailableError

DOCKER_SOCKET = "/var/run/docker.sock"


class DockerStackDriver(StackDriver):
    """The VE containers on one Docker host. Every call is safe to repeat.

    Swarm (several machines) is not handled here yet: the per-VE Kurrent and
    Mongo would need pinning to one node (see upgrade.md 6.8)."""

    def __init__(self, client: Optional[docker.DockerClient] = None, restart_policy: str = "unless-stopped") -> None:
        self._client = client
        self._restart_policy = restart_policy

    @property
    def _docker(self) -> docker.DockerClient:
        if self._client is None:
            try:
                self._client = docker.from_env()
            except docker.errors.DockerException as exc:
                raise UnavailableError(f"Docker is not reachable: {exc}")
        return self._client

    # ── networks ─────────────────────────────────────────────────────────────
    def ensure_network(self, name: str, labels: Dict[str, str]) -> None:
        if not self._docker.networks.list(names=[name]):
            self._docker.networks.create(name, driver="bridge", labels=labels, check_duplicate=True)

    def remove_network(self, name: str) -> None:
        for network in self._docker.networks.list(names=[name]):
            try:
                network.remove()
            except docker.errors.NotFound:
                pass

    def connect(self, network: str, container: str) -> None:
        try:
            net = self._docker.networks.get(network)
            net.reload()
            attached = {c.name for c in net.containers}
            if container not in attached:
                net.connect(container)
        except docker.errors.NotFound:
            pass                       # the container (e.g. axo-storage in local dev) isn't here
        except docker.errors.APIError as exc:
            if "already exists" not in str(exc):
                raise

    def disconnect(self, network: str, container: str) -> None:
        try:
            self._docker.networks.get(network).disconnect(container, force=True)
        except (docker.errors.NotFound, docker.errors.APIError):
            pass

    # ── containers ───────────────────────────────────────────────────────────
    def state(self, name: str) -> Optional[str]:
        try:
            return self._docker.containers.get(name).status
        except docker.errors.NotFound:
            return None

    def run(self, spec: ContainerRun) -> None:
        volumes = {volume: {"bind": path, "mode": "rw"} for volume, path in spec.volumes.items()}
        if spec.mount_docker_socket:
            volumes[DOCKER_SOCKET] = {"bind": DOCKER_SOCKET, "mode": "rw"}
        first, *others = spec.networks or [None]
        container = self._docker.containers.create(
            image=spec.image, name=spec.name, environment=spec.env, labels=spec.labels, network=first,
            volumes=volumes or None, command=spec.command,
            nano_cpus=int(spec.cpu_cores * 1e9) if spec.cpu_cores else None,
            mem_limit=f"{spec.memory_mb}m" if spec.memory_mb else None,
            restart_policy={"Name": self._restart_policy}, detach=True,
        )
        for network in others:
            self._docker.networks.get(network).connect(container)
        container.start()

    def start(self, name: str) -> None:
        self._docker.containers.get(name).start()

    def stop(self, name: str, timeout_s: int = 10) -> None:
        try:
            self._docker.containers.get(name).stop(timeout=timeout_s)
        except docker.errors.NotFound:
            pass

    def remove(self, name: str) -> None:
        try:
            self._docker.containers.get(name).remove(force=True)
        except docker.errors.NotFound:
            pass

    def list_labelled(self) -> List[ContainerInfo]:
        return [ContainerInfo(c.name, c.status, dict(c.labels or {}))
                for c in self._docker.containers.list(all=True, filters={"label": "axo.role"})]

    def logs(self, name: str, lines: int) -> str:
        try:
            return self._docker.containers.get(name).logs(tail=lines).decode("utf-8", "replace")
        except docker.errors.NotFound:
            return ""

    def copy_out(self, name: str, path: str) -> Optional[bytes]:
        try:
            stream, _ = self._docker.containers.get(name).get_archive(path)
        except docker.errors.NotFound:
            return None
        buffer = io.BytesIO()
        for chunk in stream:
            buffer.write(chunk)
        return buffer.getvalue()

    def remove_volume(self, name: str) -> None:
        try:
            self._docker.volumes.get(name).remove(force=True)
        except docker.errors.NotFound:
            pass
