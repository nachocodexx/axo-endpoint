"""In-memory stand-ins for Xolo, Docker, the VEMs and axo_storage -- used by
the tests (and handy for trying the API without the real services). They
follow the same rules as the real ones where it matters (owner exclusivity,
union of grants, sessions that stop working after logout)."""

from __future__ import annotations

import hashlib
import threading
import time
import uuid
from typing import Any, Callable, Dict, List, Set, Tuple

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
        # image tag -> the build it points at now (tests "rebuild" by changing it)
        self.image_ids: Dict[str, str] = {}

    def ensure_network(self, name, labels):
        created = name not in self.networks
        self.networks.setdefault(name, set())
        return created

    def remove_network(self, name):
        self.networks.pop(name, None)

    def connect(self, network, container):
        members = self.networks.setdefault(network, set())
        attached = container not in members
        members.add(container)
        return attached

    def disconnect(self, network, container):
        self.networks.get(network, set()).discard(container)

    def state(self, name):
        c = self.containers.get(name)
        return c["state"] if c else None

    def run(self, spec: ContainerRun):
        if spec.name in self.containers:
            raise ConflictError(f"container {spec.name} exists")
        self.containers[spec.name] = {"spec": spec, "state": "running", "labels": dict(spec.labels),
                                      "image_id": self.image_ids.get(spec.image, spec.image)}
        self.volumes.update(spec.volumes)
        for network in spec.networks:
            self.networks.setdefault(network, set()).add(spec.name)

    def image_outdated(self, name, image):
        c = self.containers.get(name)
        return bool(c and c.get("image_id")) and c["image_id"] != self.image_ids.get(image, image)

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
        self.repairs: List[Dict[str, Any]] = []

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

    def repair(self, ve_id, by, steps, automatic):
        self._s(ve_id)
        self.calls.append(("repair", ve_id))
        self.repairs.append({"ve_id": ve_id, "by": by, "steps": list(steps), "automatic": automatic})
        states = self.endpoints.get(ve_id, {})
        failed = [e for e, state in states.items() if state == "failed"]
        counts: Dict[str, int] = {}
        for state in states.values():
            counts[state] = counts.get(state, 0) + 1
        return {"lifecycle": self._s(ve_id)["lifecycle"], "retried": failed, "started": False,
                "endpoints": counts, "target_endpoints": len(states) or None}

    def endpoint_ids(self, ve_id):
        self._s(ve_id)
        return dict(self.endpoints.get(ve_id, {}))

    def submit_job(self, ve_id, request):
        self._s(ve_id)
        self.jobs.append((ve_id, dict(request)))
        return {"job_id": request.get("body", {}).get("job_id"), "status": "queued"}


class FakeStorage(StorageGateway):
    """axo_storage in memory: buckets, plus the files the bucket pages read
    and write (versions, uploads in pieces, folders from "/"), as the real
    gateway answers them."""

    def __init__(self, now_fn: Callable[[], float] = time.time) -> None:
        self.buckets: Dict[str, Dict[str, Any]] = {}
        self.purged: List[str] = []
        self.objects: Dict[Tuple[str, str], bytes] = {}
        # (bucket, name) -> {version: record}; a record's "pieces" holds an
        # upload's pieces until the last one arrives.
        self.files: Dict[Tuple[str, str], Dict[int, Dict[str, Any]]] = {}
        self._now_fn = now_fn

    def create_bucket(self, full_name, acting_user, quota_bytes):
        if full_name in self.buckets:
            raise ConflictError("bucket exists")
        self.buckets[full_name] = {"owner": acting_user, "quota_bytes": quota_bytes, "bytes": 0}

    def purge_bucket(self, full_name):
        self.buckets.pop(full_name, None)
        self.purged.append(full_name)
        for key in [k for k in self.files if k[0] == full_name]:
            self.files.pop(key)

    def usage_bytes(self, full_name):
        return int(self.buckets.get(full_name, {}).get("bytes", 0))

    def put_object(self, bucket_key, name, data):
        self.objects[(bucket_key, name)] = bytes(data)

    def bucket_stats(self, keys):
        out = {}
        for key in keys:
            rows = [r for (b, _n), vs in self.files.items() if b == key for r in vs.values()]
            done = {r["name"] for r in rows if r["state"] == "complete"}
            out[key] = {"used_bytes": sum(r["total_size"] for r in rows) + self.usage_bytes(key), "files": len(done),
                        "last_change": max((r["created_at"] for r in rows), default=None)}
        return out

    # ── the bucket pages ─────────────────────────────────────────────────────
    def _record(self, bucket, name, version, size, user, piece_bytes):
        return {"bucket": bucket, "name": name, "version": version, "total_size": size, "chunk_bytes": piece_bytes,
                "total_chunks": -(-size // piece_bytes) if size else 0, "state": "uploading", "format": "raw",
                "created_at": self._now_fn(), "created_by": user, "completed_at": None, "source": None,
                "content_hash": None, "pieces": {}}

    def _public(self, record):
        return {k: v for k, v in record.items() if k != "pieces"}

    def _complete(self, record):
        record["state"] = "complete"
        record["completed_at"] = self._now_fn()
        data = b"".join(record["pieces"][i] for i in range(record["total_chunks"]))
        record["content_hash"] = hashlib.sha256(data).hexdigest()
        record["data"] = data

    def _check_room(self, bucket, size):
        quota = self.buckets.get(bucket, {}).get("quota_bytes")
        used = self.bucket_stats([bucket])[bucket]["used_bytes"]
        if quota is not None and used + size > quota:
            raise ConflictError(f"{bucket} has no room for {size} more bytes")

    def start_upload(self, user, bucket, name, size, piece_bytes):
        self._check_room(bucket, size)
        versions = self.files.setdefault((bucket, name), {})
        record = self._record(bucket, name, max(versions, default=0) + 1, size, user, piece_bytes)
        versions[record["version"]] = record
        if size == 0:
            self._complete(record)
        return self._public(record)

    def put_piece(self, user, bucket, name, version, index, data):
        record = self.files.get((bucket, name), {}).get(version)
        if record is None:
            raise NotFoundError(f"no upload {name} v{version}")
        if record["state"] != "uploading" or not 0 <= index < record["total_chunks"]:
            raise ConflictError(f"piece {index} doesn't fit {name} v{version}")
        record["pieces"][index] = bytes(data)
        if len(record["pieces"]) == record["total_chunks"]:
            self._complete(record)
        return {**self._public(record), "received": len(record["pieces"])}

    def upload(self, user, bucket, name, data):
        record = self.start_upload(user, bucket, name, len(data), max(len(data), 1))
        if data:
            return self.put_piece(user, bucket, name, record["version"], 0, data)
        return record

    def list_files(self, user, bucket, prefix="", after=None, limit=200, folders=True):
        entries: Dict[str, Dict[str, Any]] = {}
        for (b, name), versions in sorted(self.files.items()):
            if b != bucket or not name.startswith(prefix) or not versions:
                continue
            rest = name[len(prefix):]
            if folders and "/" in rest:
                folder = prefix + rest.split("/", 1)[0] + "/"
                entries.setdefault(folder, {"kind": "folder", "name": folder, "files": 0})["files"] += 1
                continue
            newest = versions[max(versions)]
            complete = [v for v, r in versions.items() if r["state"] == "complete"]
            entries[name] = {**self._public(newest), "kind": "file", "versions": len(versions),
                             "latest": newest["version"], "latest_complete": max(complete, default=None)}
        order = sorted(entries, key=lambda n: (not n.endswith("/") or not folders, n))
        if after is not None:
            key = (not after.endswith("/") or not folders, after)
            order = [n for n in order if (not n.endswith("/") or not folders, n) > key]
        page = order[:limit]
        return {"entries": [entries[n] for n in page], "next": page[-1] if len(order) > limit else None}

    def versions(self, user, bucket, name):
        return [self._public(r) for _v, r in sorted(self.files.get((bucket, name), {}).items())]

    def object_info(self, user, bucket, name, version):
        versions = self.files.get((bucket, name), {})
        if version is None:
            done = [v for v, r in versions.items() if r["state"] == "complete"]
            version = max(done, default=None)
        if version not in versions:
            raise NotFoundError(f"no {name} in {bucket}")
        return self._public(versions[version])

    def download(self, user, bucket, name, version):
        record = self.files[(bucket, name)][self.object_info(user, bucket, name, version)["version"]]
        data = record.get("data", b"")
        for start in range(0, len(data), 4):     # several chunks, like the real one
            yield data[start:start + 4]

    def delete_object(self, user, bucket, name, version):
        versions = self.files.get((bucket, name), {})
        gone = sorted(versions) if version is None else [version] if version in versions else []
        if not gone:
            raise NotFoundError(f"no {name} in {bucket}")
        for v in gone:
            versions.pop(v)
        if not versions:
            self.files.pop((bucket, name), None)
        return {"bucket": bucket, "name": name, "versions": gone}
