"""In-memory stand-ins for Xolo, Docker, the VEMs and axo_storage -- used by
the tests (and handy for trying the API without the real services). They
follow the same rules as the real ones where it matters (owner exclusivity,
union of grants, sessions that stop working after logout)."""

from __future__ import annotations

import threading
import uuid
from typing import Any, Dict, List, Set, Tuple

from axo_control_plane.application.ports import (
    ContainerInfo,
    ContainerRun,
    Grant,
    IdentityProvider,
    IdentityUser,
    Leases,
    Session,
    StackDriver,
    StorageGateway,
    VemGateway,
)
from axo_control_plane.domain.errors import ConflictError, NotFoundError, UnauthorizedError, UnavailableError


class InMemoryIdentity(IdentityProvider):
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.users: Dict[str, Dict[str, Any]] = {}            # user_id -> {username, password, ...}
        self.sessions: Dict[Tuple[str, str], str] = {}       # (token, secret) -> user_id
        self.resources: Dict[str, Dict[str, Grant]] = {}     # resource -> principal_id -> grant
        self.groups: Dict[str, Dict[str, Any]] = {}          # group_id -> {name, owner, members}
        self.down = False
        self.calls: List[str] = []

    def _check_up(self, call: str) -> None:
        self.calls.append(call)
        if self.down:
            raise UnavailableError("xolo is down")

    # people
    def signup(self, username, email, password, first_name="", last_name=""):
        self._check_up("signup")
        with self._lock:
            if any(u["username"] == username for u in self.users.values()):
                raise ConflictError("user exists")
            user_id = f"u-{uuid.uuid4().hex[:8]}"
            self.users[user_id] = {"username": username, "email": email, "password": password,
                                   "first_name": first_name, "last_name": last_name, "disabled": False}
        return self._user(user_id)

    def _user(self, user_id: str) -> IdentityUser:
        u = self.users[user_id]
        return IdentityUser(user_id, u["username"], u["email"], u["first_name"], u["last_name"], u["disabled"])

    def login(self, username, password):
        self._check_up("login")
        for user_id, u in self.users.items():
            if u["username"] == username and u["password"] == password:
                token, secret = uuid.uuid4().hex, uuid.uuid4().hex
                self.sessions[(token, secret)] = user_id
                return Session(self._user(user_id), token, secret)
        raise UnauthorizedError("incorrect username or password")

    def introspect(self, access_token, temporal_secret):
        self._check_up("introspect")
        user_id = self.sessions.get((access_token, temporal_secret))
        if user_id is None:
            raise UnauthorizedError("invalid or expired session")
        return self._user(user_id)

    def refresh(self, access_token, temporal_secret):
        user = self.introspect(access_token, temporal_secret)
        del self.sessions[(access_token, temporal_secret)]
        token, secret = uuid.uuid4().hex, uuid.uuid4().hex
        self.sessions[(token, secret)] = user.user_id
        return Session(user, token, secret)

    def logout(self, access_token, temporal_secret, username):
        self._check_up("logout")
        self.sessions.pop((access_token, temporal_secret), None)

    def change_password(self, username, access_token, temporal_secret, new_password):
        user = self.introspect(access_token, temporal_secret)
        self.users[user.user_id]["password"] = new_password

    # resources
    def claim(self, resource, owner_id, owner_type="USER"):
        self._check_up("claim")
        grants = self.resources.setdefault(resource, {})
        if any(g.is_owner for g in grants.values()):
            raise ConflictError("already owned")
        grants[owner_id] = Grant(owner_id, owner_type, frozenset({"sys:manage", "read", "write", "delete"}), True)

    def grant(self, resource, principal_id, principal_type, permissions):
        self._check_up("grant")
        grants = self.resources.setdefault(resource, {})
        old = grants.get(principal_id)
        perms = frozenset(permissions) | (old.permissions if old else frozenset())
        grants[principal_id] = Grant(principal_id, principal_type, perms, old.is_owner if old else False)

    def revoke(self, resource, principal_id, principal_type, permissions):
        self._check_up("revoke")
        grants = self.resources.get(resource, {})
        old = grants.get(principal_id)
        if old is None:
            return
        remaining = old.permissions - frozenset(permissions)
        if remaining or old.is_owner:
            grants[principal_id] = Grant(principal_id, old.principal_type, remaining, old.is_owner)
        else:
            del grants[principal_id]

    def grants(self, resource):
        self._check_up("grants")
        return list(self.resources.get(resource, {}).values())

    def transfer(self, resource, principal_id, principal_type):
        self._check_up("transfer")
        grants = self.resources.get(resource)
        if grants is None:
            raise NotFoundError("no such resource")
        for pid, g in list(grants.items()):
            if g.is_owner:
                del grants[pid]
        grants[principal_id] = Grant(principal_id, principal_type,
                                     frozenset({"sys:manage", "read", "write", "delete"}), True)

    def delete_resource(self, resource):
        self._check_up("delete_resource")
        self.resources.pop(resource, None)

    def resources_of(self, user_id):
        self._check_up("resources_of")
        principals = {user_id, *self._groups_of(user_id)}
        return [r for r, grants in self.resources.items() if principals & set(grants)]

    # groups
    def create_group(self, name, owner_id):
        self._check_up("create_group")
        group_id = f"g-{uuid.uuid4().hex[:8]}"
        self.groups[group_id] = {"name": name, "owner": owner_id, "members": set()}
        return group_id

    def delete_group(self, group_id):
        self._check_up("delete_group")
        self.groups.pop(group_id, None)

    def add_members(self, group_id, user_ids):
        self._check_up("add_members")
        self.groups[group_id]["members"].update(user_ids)

    def remove_members(self, group_id, user_ids):
        self._check_up("remove_members")
        self.groups[group_id]["members"].difference_update(user_ids)

    def transfer_group(self, group_id, user_id):
        self._check_up("transfer_group")
        self.groups[group_id]["owner"] = user_id

    def groups_of(self, user_id):
        self._check_up("groups_of")
        return self._groups_of(user_id)

    def _groups_of(self, user_id: str) -> List[str]:
        return [gid for gid, g in self.groups.items() if user_id in g["members"]]


class InMemoryLeases(Leases):
    def __init__(self) -> None:
        self.holders: Dict[str, str] = {}

    def acquire(self, name, holder, seconds):
        current = self.holders.get(name)
        if current not in (None, holder):
            return False
        self.holders[name] = holder
        return True

    def release(self, name, holder):
        if self.holders.get(name) == holder:
            del self.holders[name]


class InMemoryStackDriver(StackDriver):
    def __init__(self) -> None:
        self.networks: Dict[str, Set[str]] = {}
        self.containers: Dict[str, Dict[str, Any]] = {}
        self.volumes: Set[str] = set()
        self.removed: List[str] = []
        self.files: Dict[Tuple[str, str], bytes] = {}

    def ensure_network(self, name, labels):
        self.networks.setdefault(name, set())

    def remove_network(self, name):
        self.networks.pop(name, None)

    def connect(self, network, container):
        self.networks.setdefault(network, set()).add(container)

    def disconnect(self, network, container):
        self.networks.get(network, set()).discard(container)

    def state(self, name):
        c = self.containers.get(name)
        return c["state"] if c else None

    def run(self, spec: ContainerRun):
        if spec.name in self.containers:
            raise ConflictError(f"container {spec.name} exists")
        self.containers[spec.name] = {"spec": spec, "state": "running", "labels": dict(spec.labels)}
        self.volumes.update(spec.volumes)
        for network in spec.networks:
            self.networks.setdefault(network, set()).add(spec.name)

    def start(self, name):
        self.containers[name]["state"] = "running"

    def stop(self, name, timeout_s=10):
        if name in self.containers:
            self.containers[name]["state"] = "exited"

    def remove(self, name):
        if self.containers.pop(name, None) is not None:
            self.removed.append(name)
        for members in self.networks.values():
            members.discard(name)

    def list_labelled(self):
        return [ContainerInfo(n, c["state"], c["labels"]) for n, c in self.containers.items()
                if "axo.role" in c["labels"]]

    def logs(self, name, lines):
        return f"last {lines} lines of {name}"

    def copy_out(self, name, path):
        return self.files.get((name, path))

    def remove_volume(self, name):
        self.volumes.discard(name)

    def add_labelled(self, name: str, labels: Dict[str, str], state: str = "running") -> None:
        """Test helper: a container created by someone else (e.g. a VEM)."""
        self.containers[name] = {"spec": None, "state": state, "labels": dict(labels)}


class FakeVem(VemGateway):
    """One fake VEM per VE id, following the real VEM's lifecycle answers."""

    def __init__(self) -> None:
        self.state: Dict[str, Dict[str, Any]] = {}
        self.calls: List[Tuple[str, str]] = []
        self.unreachable: Set[str] = set()
        self.jobs: List[Tuple[str, Dict[str, Any]]] = []
        self.endpoints: Dict[str, Dict[str, str]] = {}

    def _s(self, ve_id: str) -> Dict[str, Any]:
        if ve_id in self.unreachable:
            raise UnavailableError(f"VEM of {ve_id} unreachable")
        return self.state.setdefault(ve_id, {"lifecycle": "Running", "ready_for_removal": False})

    def status(self, ve_id):
        return dict(self._s(ve_id))

    def stop(self, ve_id, by):
        self.calls.append(("stop", ve_id))
        self._s(ve_id)["lifecycle"] = "Stopped"

    def start(self, ve_id, by):
        self.calls.append(("start", ve_id))
        self._s(ve_id)["lifecycle"] = "Running"

    def prepare_delete(self, ve_id, by):
        self.calls.append(("prepare_delete", ve_id))
        s = self._s(ve_id)
        s["lifecycle"] = "Deleting"
        s["ready_for_removal"] = True

    def set_limits(self, ve_id, limits, by):
        self.calls.append(("set_limits", ve_id))
        self._s(ve_id)["limits"] = dict(limits)

    def endpoint_ids(self, ve_id):
        self._s(ve_id)
        return dict(self.endpoints.get(ve_id, {}))

    def submit_job(self, ve_id, request):
        self._s(ve_id)
        self.jobs.append((ve_id, dict(request)))
        return {"job_id": request.get("body", {}).get("job_id"), "status": "queued"}


class FakeStorage(StorageGateway):
    def __init__(self) -> None:
        self.buckets: Dict[str, Dict[str, Any]] = {}
        self.purged: List[str] = []
        self.objects: Dict[Tuple[str, str], bytes] = {}

    def create_bucket(self, full_name, acting_user, quota_bytes):
        if full_name in self.buckets:
            raise ConflictError("bucket exists")
        self.buckets[full_name] = {"owner": acting_user, "quota_bytes": quota_bytes, "bytes": 0}

    def purge_bucket(self, full_name):
        self.buckets.pop(full_name, None)
        self.purged.append(full_name)

    def usage_bytes(self, full_name):
        return int(self.buckets.get(full_name, {}).get("bytes", 0))

    def put_object(self, bucket_key, name, data):
        self.objects[(bucket_key, name)] = bytes(data)
