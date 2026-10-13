from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from pymongo.database import Database

from axo_control_plane.application import notifications as N
from axo_control_plane.application.capacity import CapacityLedger
from axo_control_plane.application.catalog import PlanCatalog
from axo_control_plane.application.notifications import Notifications
from axo_control_plane.application.ports import (
    ContainerRun,
    HistoryLog,
    IdentityProvider,
    Leases,
    StackDriver,
    VemGateway,
)
from axo_control_plane.domain import roles as R
from axo_control_plane.domain import virtual_environment as V
from axo_control_plane.domain.errors import DomainError, UnavailableError
from axo_control_plane.domain.virtual_environment import VirtualEnvironment

VEM_OUTBOX_PATH = "/var/lib/axo_vem/outbox"


@dataclass
class StackSettings:
    """Everything a VE's containers need that comes from the platform."""

    vem_image: str
    endpoint_image: str
    kurrent_image: str
    mongo_image: str
    control_network: str
    storage_container: str
    storage_uri: str
    cp_intake_uri: str
    container_backend: str = "docker"
    vem_token_fn: Callable[[str], str] = lambda ve_id: ""
    storage_token_fn: Callable[[str], str] = lambda ve_id: ""
    platform_limits: Dict[str, Any] = field(default_factory=dict)
    # Creates the VE's platform code bucket (axo/ve-<id>-functions) in
    # axo_storage; the VEM only reaches it with its own VE token.
    ensure_code_bucket: Callable[[str], None] = lambda ve_id: None


def labels_for(ve_id: str, role: str) -> Dict[str, str]:
    return {"axo.ve": ve_id, "axo.role": role, "axo.managed-by": "axo-control-plane"}


class Provisioner:
    """Makes every VE's containers match what the VE should be (desired
    state). Each tick looks at reality and does only the next missing step,
    so repeating any step is harmless and a crashed copy's work is simply
    picked up by another copy after its per-VE lease expires.

    - running:  network -> axo_storage attached -> Kurrent + Mongo -> VEM
                (which creates the endpoints) -> limits pushed -> Running.
    - stopped / sleeping: the VEM stops the VE (drains, flushes usage), then
                its containers are stopped but kept (with their volumes).
    - deleted:  the VEM prepares (ready_for_removal, outbox empty or copied),
                then every container of the VE and its network are removed,
                capacity is released; volumes go after the grace period.
    """

    def __init__(self, db: Database, driver: StackDriver, vem: VemGateway, leases: Leases,
                 capacity: CapacityLedger, catalog: PlanCatalog, identity: IdentityProvider,
                 notifications: Notifications, history: HistoryLog, settings: StackSettings, instance_id: str,
                 lease_s: float = 30.0, heartbeat_timeout_s: float = 30.0, vem_recreate_after_s: float = 300.0,
                 delete_flush_timeout_s: float = 600.0, grace_s: float = 30 * 86400.0,
                 salvage: Optional[Callable[[str, bytes], int]] = None,
                 now_fn: Callable[[], float] = time.time) -> None:
        self._col = db["virtual_environments"]
        self._driver = driver
        self._vem = vem
        self._leases = leases
        self._capacity = capacity
        self._catalog = catalog
        self._identity = identity
        self._notifications = notifications
        self._history = history
        self._s = settings
        self._instance_id = instance_id
        self._lease_s = lease_s
        self._heartbeat_timeout_s = heartbeat_timeout_s
        self._vem_recreate_after_s = vem_recreate_after_s
        self._delete_flush_timeout_s = delete_flush_timeout_s
        self._grace_s = grace_s
        # Reads a copied VEM outbox (tar bytes) into the usage ledger.
        self._salvage = salvage
        self._now_fn = now_fn
        self.last_errors: Dict[str, str] = {}

    # ── loop ─────────────────────────────────────────────────────────────────
    def run_forever(self, stop_event: threading.Event, interval_s: float = 5.0) -> None:
        while not stop_event.wait(interval_s):
            try:
                self.tick()
            except Exception as exc:   # one bad tick must not stop provisioning
                self.last_errors["*"] = repr(exc)

    def tick(self) -> int:
        worked = 0
        for doc in self._col.find({"lifecycle": {"$ne": V.DELETED}}):
            ve = VirtualEnvironment.from_doc(doc)
            if not self._leases.acquire(f"ve:{ve.ve_id}", self._instance_id, self._lease_s):
                continue
            worked += 1
            try:
                self.step(ve)
                self.last_errors.pop(ve.ve_id, None)
            except Exception as exc:
                self.last_errors[ve.ve_id] = str(exc)
                self._update(ve.ve_id, step_error=str(exc))
        return worked

    def step(self, ve: VirtualEnvironment) -> None:
        if ve.desired == V.DESIRED_RUNNING:
            self._bring_up(ve)
        elif ve.desired in (V.DESIRED_STOPPED, V.DESIRED_SLEEPING):
            self._wind_down(ve)
        elif ve.desired == V.DESIRED_DELETED:
            self._delete(ve)

    # ── running ──────────────────────────────────────────────────────────────
    def _bring_up(self, ve: VirtualEnvironment) -> None:
        d = self._driver
        d.ensure_network(ve.network, labels_for(ve.ve_id, "network"))
        d.connect(ve.network, self._s.storage_container)
        doc = self._col.find_one({"_id": ve.ve_id}, {"code_bucket": 1}) or {}
        if not doc.get("code_bucket"):
            self._s.ensure_code_bucket(ve.ve_id)
            self._col.update_one({"_id": ve.ve_id}, {"$set": {"code_bucket": True}})
        for role in ("kurrent", "mongo"):
            self._ensure_container(ve, role)
        fresh_vem = self._ensure_container(ve, "vem")
        if fresh_vem:
            self._update(ve.ve_id, step="vem started")
            return
        if not self._vem_alive(ve):
            return
        ve = self._reload(ve.ve_id)
        doc = self._col.find_one({"_id": ve.ve_id}) or {}
        if doc.get("limits_dirty"):
            self._vem.set_limits(ve.ve_id, self.vem_limits(ve), by="control-plane")
            self._col.update_one({"_id": ve.ve_id}, {"$set": {"limits_dirty": False}})
        vem_lifecycle = (ve.heartbeat or {}).get("lifecycle")
        if vem_lifecycle in (V.STOPPED, V.FAILED):
            # Containers are back after a stop / sleep: the VEM restarts its
            # endpoints and restores the functions.
            if not doc.get("start_requested"):
                self._vem.start(ve.ve_id, by="control-plane")
                self._col.update_one({"_id": ve.ve_id}, {"$set": {"start_requested": True}})
            return
        self._col.update_one({"_id": ve.ve_id}, {"$set": {"start_requested": False, "stop_requested": False}})
        if vem_lifecycle in (V.RUNNING, V.SCALING) and ve.lifecycle not in (V.RUNNING, V.SCALING):
            was = ve.lifecycle
            self._update(ve.ve_id, lifecycle=vem_lifecycle, health=(ve.heartbeat or {}).get("health"),
                         lifecycle_reason="", step="running", step_error="", wake_requested_at=None)
            text = {V.CREATING: f"VE {ve.name} is ready", V.WAKING: f"VE {ve.name} woke up"}.get(
                was, f"VE {ve.name} is running")
            self._notify_owner(ve, N.VE_STATE, text, {"ve_id": ve.ve_id, "lifecycle": vem_lifecycle})
            self._history.append(f"ve-{ve.ve_id}", "VirtualEnvironmentLifecycleChanged",
                                 {"ve_id": ve.ve_id, "from": was, "to": vem_lifecycle, "at": self._now_fn()})

    def _ensure_container(self, ve: VirtualEnvironment, role: str) -> bool:
        """True if the container was just created or started."""
        name = ve.container(role)
        state = self._driver.state(name)
        if state == "running":
            return False
        if state is None:
            self._driver.run(self._spec(ve, role))
        else:
            self._driver.start(name)
        if role == "vem":
            self._update(ve.ve_id, vem_started_at=self._now_fn())
        return True

    def _vem_alive(self, ve: VirtualEnvironment) -> bool:
        """Heartbeats are the VEM's sign of life. No heartbeat for a while =
        Unavailable; still nothing after much longer = the VEM container is
        recreated (its data is in the VE's Kurrent / Mongo and its outbox
        volume, so nothing is lost)."""
        now = self._now_fn()
        last = ve.heartbeat_at or 0.0
        if now - last <= self._heartbeat_timeout_s:
            if ve.vem_down_since is not None:
                self._update(ve.ve_id, vem_down_since=None)
            return True
        started = (self._col.find_one({"_id": ve.ve_id}) or {}).get("vem_started_at") or 0.0
        if now - started <= self._heartbeat_timeout_s * 2:
            return False                      # just (re)started: give it time
        down_since = ve.vem_down_since or now
        if ve.vem_down_since is None:
            self._update(ve.ve_id, vem_down_since=now, health="Unavailable",
                         lifecycle_reason="the VE manager is not responding")
            self._history.append(f"ve-{ve.ve_id}", "VemDown", {"ve_id": ve.ve_id, "at": now})
        elif now - down_since >= self._vem_recreate_after_s:
            self._driver.remove(ve.container("vem"))
            self._ensure_container(ve, "vem")
            self._update(ve.ve_id, vem_down_since=None, vem_started_at=now, step="vem recreated")
            self._history.append(f"ve-{ve.ve_id}", "VemRecreated", {"ve_id": ve.ve_id, "at": now})
        return False

    # ── stopped / sleeping ───────────────────────────────────────────────────
    def _wind_down(self, ve: VirtualEnvironment) -> None:
        target = V.SLEEPING if ve.desired == V.DESIRED_SLEEPING else V.STOPPED
        vem_name = ve.container("vem")
        if self._driver.state(vem_name) == "running":
            status = self._vem_status_or_none(ve)
            if status is not None and status.get("lifecycle") != V.STOPPED:
                doc = self._col.find_one({"_id": ve.ve_id}) or {}
                if not doc.get("stop_requested"):
                    self._vem.stop(ve.ve_id, by="control-plane")
                    self._col.update_one({"_id": ve.ve_id}, {"$set": {"stop_requested": True}})
                    self._update(ve.ve_id, step="vem stopping endpoints")
                return
            # Stopped (or unreachable: nothing to drain through it).
        for role in ("vem", "mongo", "kurrent"):
            if self._driver.state(ve.container(role)) == "running":
                self._driver.stop(ve.container(role))
        if ve.lifecycle != target:
            now = self._now_fn()
            self._update(ve.ve_id, lifecycle=target, health=None, step="stopped", stopped_at=now,
                         lifecycle_reason="", start_requested=False, stop_requested=False)
            self._history.append(f"ve-{ve.ve_id}", "VirtualEnvironmentLifecycleChanged",
                                 {"ve_id": ve.ve_id, "from": ve.lifecycle, "to": target, "at": now})

    # ── deleted ──────────────────────────────────────────────────────────────
    def _delete(self, ve: VirtualEnvironment) -> None:
        now = self._now_fn()
        doc = self._col.find_one({"_id": ve.ve_id}) or {}
        vem_name = ve.container("vem")
        if self._driver.state(vem_name) is not None and not doc.get("vem_settled"):
            if self._driver.state(vem_name) != "running":
                self._driver.start(vem_name)
                for role in ("kurrent", "mongo"):
                    self._ensure_container(ve, role)
                return
            status = self._vem_status_or_none(ve)
            if status is not None and not status.get("ready_for_removal"):
                if not doc.get("delete_requested_at"):
                    self._vem.prepare_delete(ve.ve_id, by="control-plane")
                    self._col.update_one({"_id": ve.ve_id}, {"$set": {"delete_requested_at": now}})
                    self._update(ve.ve_id, step="vem winding down")
                if now - (doc.get("delete_requested_at") or now) < self._delete_flush_timeout_s:
                    return
            pending = int((ve.heartbeat or {}).get("outbox_pending", 0))
            started = doc.get("delete_requested_at") or now
            if pending and now - started < self._delete_flush_timeout_s:
                self._update(ve.ve_id, step=f"waiting for {pending} unsent message(s)")
                return
            if pending or status is None:
                self._salvage_outbox(ve)
            self._col.update_one({"_id": ve.ve_id}, {"$set": {"vem_settled": True}})
        # Remove every container of the VE (VEM, Kurrent, Mongo, endpoints,
        # runners), then the network. Volumes stay for the grace period.
        for info in self._driver.list_labelled():
            if info.labels.get("axo.ve") == ve.ve_id:
                self._driver.remove(info.name)
        self._driver.disconnect(ve.network, self._s.storage_container)
        self._driver.remove_network(ve.network)
        self._capacity.release(ve.reserved)
        try:
            self._identity.delete_resource(R.ve_resource(ve.ve_id))
        except DomainError:
            pass
        self._update(ve.ve_id, lifecycle=V.DELETED, health=None, deleted_at=now,
                     purge_after=now + self._grace_s, step="deleted")
        self._history.append(f"ve-{ve.ve_id}", "VirtualEnvironmentDeleted",
                             {"ve_id": ve.ve_id, "released": ve.reserved.to_dict(), "at": now})
        self._notify_owner(ve, N.VE_STATE, f"VE {ve.name} was deleted", {"ve_id": ve.ve_id})

    def _salvage_outbox(self, ve: VirtualEnvironment) -> None:
        if self._salvage is None:
            return
        data = self._driver.copy_out(ve.container("vem"), VEM_OUTBOX_PATH)
        if data:
            count = self._salvage(ve.ve_id, data)
            self._history.append(f"ve-{ve.ve_id}", "VemOutboxSalvaged",
                                 {"ve_id": ve.ve_id, "messages": count, "at": self._now_fn()})

    # ── container specs ──────────────────────────────────────────────────────
    def vem_limits(self, ve: VirtualEnvironment) -> Dict[str, Any]:
        plan = self._catalog.get(ve.plan_id)
        return {
            "max_endpoints": plan.max_endpoints,
            "policies_allowed": plan.policies_allowed,
            "max_retention_days": plan.max_retention_days,
            "quota_cpu_cores": ve.quota.cpu_cores,
            "quota_memory_mb": ve.quota.memory_mb,
            **self._s.platform_limits,
        }

    def _spec(self, ve: VirtualEnvironment, role: str) -> ContainerRun:
        plan = self._catalog.get(ve.plan_id)
        base = plan.base
        labels = labels_for(ve.ve_id, role)
        if role == "kurrent":
            return ContainerRun(
                name=ve.container(role), image=self._s.kurrent_image, networks=[ve.network], labels=labels,
                # Same single-node, TLS-off setup as the dev stack's kurrentdb:
                # safe only because the VE network is private.
                env={"EVENTSTORE_INSECURE": "true", "EVENTSTORE_RUN_PROJECTIONS": "All",
                     "EVENTSTORE_CLUSTER_SIZE": "1", "EVENTSTORE_TELEMETRY_OPTOUT": "true"},
                command=["--insecure", "--ext-ip", "0.0.0.0"],
                volumes={f"ve-{ve.ve_id}-kurrent-data": "/var/lib/eventstore"},
                cpu_cores=round(base.cpu_cores * 0.4, 2), memory_mb=int(base.memory_mb * 0.4),
            )
        if role == "mongo":
            return ContainerRun(
                name=ve.container(role), image=self._s.mongo_image, networks=[ve.network], labels=labels,
                volumes={f"ve-{ve.ve_id}-mongo-data": "/data/db"},
                cpu_cores=round(base.cpu_cores * 0.3, 2), memory_mb=int(base.memory_mb * 0.3),
            )
        limits = self.vem_limits(ve)
        env = {
            "AXO_VEM_VE_ID": ve.ve_id,
            "AXO_VEM_SERVICE_TOKEN": self._s.vem_token_fn(ve.ve_id),
            "AXO_VEM_ROUTER_BIND": "tcp://0.0.0.0:6000",
            "AXO_VEM_HTTP_HOST": "0.0.0.0",
            "AXO_VEM_HTTP_PORT": "8080",
            "AXO_VEM_KURRENT_URI": f"esdb://{ve.container('kurrent')}:2113?tls=false",
            "AXO_VEM_MONGO_URI": f"mongodb://{ve.container('mongo')}:27017",
            "AXO_VEM_MONGO_DB_NAME": "axo_vem",
            "AXO_VEM_PLAN_MAX_ENDPOINTS": str(limits["max_endpoints"]),
            "AXO_VEM_PLAN_POLICIES_ALLOWED": str(limits["policies_allowed"]).lower(),
            "AXO_VEM_PLAN_MAX_RETENTION_DAYS": str(limits["max_retention_days"]),
            "AXO_VEM_QUOTA_CPU_CORES": str(limits["quota_cpu_cores"]),
            "AXO_VEM_QUOTA_MEMORY_MB": str(limits["quota_memory_mb"]),
            "AXO_VEM_INITIAL_SETTINGS": _json({
                "target_endpoints": ve.target_endpoints, "min_endpoints": ve.min_endpoints,
                "new_endpoint_cpu_cores": ve.endpoint_size.cpu_cores,
                "new_endpoint_memory_mb": ve.endpoint_size.memory_mb,
            }),
            "AXO_VEM_CP_URI": self._s.cp_intake_uri,
            "AXO_VEM_CP_OUTBOX_PATH": VEM_OUTBOX_PATH,
            # The VEM reaches axo_storage only with its own VE's token (never
            # the platform service token): it can't touch other VEs' data.
            "AXO_VEM_STORAGE_URI": self._s.storage_uri,
            "AXO_VEM_NODE_STORAGE_URI": self._s.storage_uri,
            "AXO_VEM_VE_STORAGE_TOKEN": self._s.storage_token_fn(ve.ve_id),
            "AXO_VEM_NODE_IMAGE": self._s.endpoint_image,
            "AXO_VEM_NODE_NETWORK": ve.network,
            "AXO_VEM_DOCKER_MODE": self._s.container_backend,
            "AXO_VEM_NODE_API_URI": f"tcp://{ve.container('vem')}:6000",
            "AXO_VEM_LOG_TO_FILE": "false",
        }
        if limits.get("max_queue_limit"):
            env["AXO_VEM_PLATFORM_MAX_QUEUE_LIMIT"] = str(limits["max_queue_limit"])
        if limits.get("max_wait_seconds"):
            env["AXO_VEM_PLATFORM_MAX_WAIT_SECONDS"] = str(limits["max_wait_seconds"])
        return ContainerRun(
            name=ve.container("vem"), image=self._s.vem_image, env=env,
            networks=[ve.network, self._s.control_network], labels=labels,
            volumes={f"ve-{ve.ve_id}-vem-outbox": VEM_OUTBOX_PATH},
            cpu_cores=round(base.cpu_cores * 0.3, 2), memory_mb=int(base.memory_mb * 0.3),
            mount_docker_socket=True,
        )

    # ── helpers ──────────────────────────────────────────────────────────────
    def _vem_status_or_none(self, ve: VirtualEnvironment) -> Optional[Dict[str, Any]]:
        try:
            return self._vem.status(ve.ve_id)
        except UnavailableError:
            return None

    def _reload(self, ve_id: str) -> VirtualEnvironment:
        return VirtualEnvironment.from_doc(self._col.find_one({"_id": ve_id}))

    def _update(self, ve_id: str, **fields: Any) -> None:
        self._col.update_one({"_id": ve_id}, {"$set": fields})

    def _notify_owner(self, ve: VirtualEnvironment, kind: str, text: str, data: Dict[str, Any]) -> None:
        owners: List[str] = [ve.owner.id] if ve.owner.kind == V.OWNER_USER else []
        if ve.owner.kind == V.OWNER_TEAM:
            team = self._col.database["teams"].find_one({"_id": ve.owner.id}) or {}
            owners = [team.get("owner_id", "")]
        self._notifications.notify(owners, kind, text, data)


def _json(value: Any) -> str:
    import json

    return json.dumps(value, separators=(",", ":"))
