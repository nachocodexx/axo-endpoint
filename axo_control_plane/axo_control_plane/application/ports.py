"""The Control Plane's boundaries: everything it talks to, as small
interfaces. Real implementations live in ``infrastructure/``; tests and local
development use the in-memory ones."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, FrozenSet, Iterable, List, Optional


# ── history (Kurrent) ─────────────────────────────────────────────────────────
class HistoryLog(ABC):
    """Append-only history of everything that happened (who did what,
    lifecycle changes, usage records). Current state lives in Mongo."""

    @abstractmethod
    def append(self, stream: str, event_type: str, data: Dict[str, Any]) -> None: ...


class InMemoryHistory(HistoryLog):
    def __init__(self) -> None:
        self.events: List[Dict[str, Any]] = []

    def append(self, stream: str, event_type: str, data: Dict[str, Any]) -> None:
        self.events.append({"stream": stream, "type": event_type, "data": dict(data)})

    def of_type(self, event_type: str) -> List[Dict[str, Any]]:
        return [e["data"] for e in self.events if e["type"] == event_type]


# ── leases (one Control Plane copy works on a thing at a time) ───────────────
class Leases(ABC):
    @abstractmethod
    def acquire(self, name: str, holder: str, seconds: float) -> bool:
        """True if ``holder`` now holds ``name`` (new, renewed, or taken over
        after it expired)."""

    @abstractmethod
    def release(self, name: str, holder: str) -> None: ...


# ── containers (Docker / Swarm) ──────────────────────────────────────────────
@dataclass(frozen=True)
class ContainerRun:
    name: str
    image: str
    env: Dict[str, str] = field(default_factory=dict)
    networks: List[str] = field(default_factory=list)
    labels: Dict[str, str] = field(default_factory=dict)
    volumes: Dict[str, str] = field(default_factory=dict)          # volume name -> mount path
    cpu_cores: Optional[float] = None
    memory_mb: Optional[int] = None
    command: Optional[List[str]] = None
    mount_docker_socket: bool = False


@dataclass(frozen=True)
class ContainerInfo:
    name: str
    state: str                       # running | exited | created | ...
    labels: Dict[str, str] = field(default_factory=dict)


class StackDriver(ABC):
    """The container operations the Control Plane needs. Every call is safe
    to repeat (creating what exists / removing what's gone is a no-op)."""

    @abstractmethod
    def ensure_network(self, name: str, labels: Dict[str, str]) -> None: ...

    @abstractmethod
    def remove_network(self, name: str) -> None: ...

    @abstractmethod
    def connect(self, network: str, container: str) -> None: ...

    @abstractmethod
    def disconnect(self, network: str, container: str) -> None: ...

    @abstractmethod
    def state(self, name: str) -> Optional[str]:
        """None = no such container."""

    @abstractmethod
    def run(self, spec: ContainerRun) -> None: ...

    @abstractmethod
    def start(self, name: str) -> None: ...

    @abstractmethod
    def stop(self, name: str, timeout_s: int = 10) -> None: ...

    @abstractmethod
    def remove(self, name: str) -> None: ...

    @abstractmethod
    def list_labelled(self) -> List[ContainerInfo]:
        """Every container carrying an ``axo.role`` label."""

    @abstractmethod
    def logs(self, name: str, lines: int) -> str: ...

    @abstractmethod
    def copy_out(self, name: str, path: str) -> Optional[bytes]:
        """A tar of ``path`` inside the container, or None."""

    @abstractmethod
    def remove_volume(self, name: str) -> None: ...


# ── a VE's VEM (HTTP, per-VE service token) ──────────────────────────────────
class VemGateway(ABC):
    @abstractmethod
    def status(self, ve_id: str) -> Dict[str, Any]:
        """GET /ve. Raises UnavailableError if the VEM can't be reached."""

    @abstractmethod
    def stop(self, ve_id: str, by: str) -> None: ...

    @abstractmethod
    def start(self, ve_id: str, by: str) -> None: ...

    @abstractmethod
    def prepare_delete(self, ve_id: str, by: str) -> None: ...

    @abstractmethod
    def set_limits(self, ve_id: str, limits: Dict[str, Any], by: str) -> None: ...

    @abstractmethod
    def endpoint_ids(self, ve_id: str) -> Dict[str, str]:
        """endpoint id -> lifecycle state, removed ones included."""

    @abstractmethod
    def submit_job(self, ve_id: str, request: Dict[str, Any]) -> Dict[str, Any]:
        """Deliver one request saved while the VE slept (see SleepManager):
        ``{"path": VEM route, "body": JSON body incl. its job_id/run_id,
        "acting_user": user id}``. Raises UnavailableError if the VEM can't
        be reached (it is retried), DomainError if the VEM refused it."""


# ── identity + roles (Xolo) ──────────────────────────────────────────────────
@dataclass(frozen=True)
class IdentityUser:
    user_id: str
    username: str
    email: str = ""
    first_name: str = ""
    last_name: str = ""
    disabled: bool = False


@dataclass(frozen=True)
class Session:
    user: IdentityUser
    access_token: str
    temporal_secret: str


@dataclass(frozen=True)
class Grant:
    principal_id: str
    principal_type: str                  # USER | GROUP
    permissions: FrozenSet[str]
    is_owner: bool = False


class IdentityProvider(ABC):
    """Xolo, seen from the Control Plane. ACL / group calls run in Xolo's
    service mode: the CP acts with its own API key, after applying axo's own
    rules (only the CP talks to Xolo)."""

    # people and sessions
    @abstractmethod
    def signup(self, username: str, email: str, password: str,
               first_name: str = "", last_name: str = "") -> IdentityUser: ...

    @abstractmethod
    def login(self, username: str, password: str) -> Session: ...

    @abstractmethod
    def introspect(self, access_token: str, temporal_secret: str) -> IdentityUser:
        """Raises UnauthorizedError for a bad / logged-out session and
        UnavailableError when Xolo can't answer."""

    @abstractmethod
    def refresh(self, access_token: str, temporal_secret: str) -> Session: ...

    @abstractmethod
    def logout(self, access_token: str, temporal_secret: str, username: str) -> None: ...

    @abstractmethod
    def change_password(self, username: str, access_token: str, temporal_secret: str, new_password: str) -> None: ...

    # resources (ve:<id>, bucket:<id>)
    @abstractmethod
    def claim(self, resource: str, owner_id: str, owner_type: str = "USER") -> None: ...

    @abstractmethod
    def grant(self, resource: str, principal_id: str, principal_type: str, permissions: Iterable[str]) -> None: ...

    @abstractmethod
    def revoke(self, resource: str, principal_id: str, principal_type: str, permissions: Iterable[str]) -> None: ...

    @abstractmethod
    def grants(self, resource: str) -> List[Grant]: ...

    @abstractmethod
    def transfer(self, resource: str, principal_id: str, principal_type: str) -> None: ...

    @abstractmethod
    def delete_resource(self, resource: str) -> None: ...

    @abstractmethod
    def resources_of(self, user_id: str) -> List[str]:
        """Resource ids the user can see (owned + shared, incl. via groups)."""

    # groups (teams, platform roles)
    @abstractmethod
    def create_group(self, name: str, owner_id: str) -> str:
        """Returns the group id."""

    @abstractmethod
    def delete_group(self, group_id: str) -> None: ...

    @abstractmethod
    def add_members(self, group_id: str, user_ids: Iterable[str]) -> None: ...

    @abstractmethod
    def remove_members(self, group_id: str, user_ids: Iterable[str]) -> None: ...

    @abstractmethod
    def transfer_group(self, group_id: str, user_id: str) -> None: ...

    @abstractmethod
    def groups_of(self, user_id: str) -> List[str]:
        """Group ids the user is a member of."""


# ── axo_storage (platform service token) ─────────────────────────────────────
class StorageGateway(ABC):
    @abstractmethod
    def create_bucket(self, full_name: str, acting_user: str, quota_bytes: Optional[int]) -> None: ...

    @abstractmethod
    def purge_bucket(self, full_name: str) -> None:
        """Remove every object for good (after the grace period)."""

    @abstractmethod
    def usage_bytes(self, full_name: str) -> int: ...

    @abstractmethod
    def put_object(self, bucket_key: str, name: str, data: bytes) -> None:
        """Store one object in a platform bucket (e.g. cleanup logs)."""
