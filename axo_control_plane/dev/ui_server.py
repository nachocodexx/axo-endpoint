"""A Control Plane for UI development: the real API and application code on
in-memory stand-ins (mongomock, the test identity / stack / VEM fakes), with
sample people, VEs, metrics and job records, and a fake VEM that keeps
heartbeating and answers the pass-through routes.

    cd axo_control_plane && poetry run python dev/ui_server.py      # http://localhost:9000

    # Faster and with 15-min / day statistics (30-day charts): a real MongoDB
    docker run -d --rm --name axo-ui-dev-mongo -p 27018:27017 mongo:8-noble
    poetry run python dev/ui_server.py --mongo-uri mongodb://localhost:27018

Sign in as demo / demo-password (owns 3 VEs, Developer on a 4th) or admin /
admin-password (the Platform Admin, must change the password first). New
job records and minute statistics keep arriving every minute so the
dashboard moves. Everything is lost on exit. Never use this for real data.
"""

from __future__ import annotations

import argparse
import json
import math
import random
import re
import threading
import time
import uuid
from typing import Any, Callable, Dict, List, Optional, Tuple

import httpx
import mongomock
import uvicorn
from axo_shared.metrics import StatSummary
from axo_shared.metrics import names as m

from axo_control_plane.application.access import AccessResolver, Invalidations
from axo_control_plane.application.authz import Authorizer
from axo_control_plane.application.billing import VeBills
from axo_control_plane.application.billing_views import BillingViews
from axo_control_plane.application.buckets import Buckets, StorageAccess
from axo_control_plane.application.capacity import CapacityLedger
from axo_control_plane.application.catalog import PlanCatalog
from axo_control_plane.application.front_door import FrontDoor
from axo_control_plane.application.intake import VemIntake
from axo_control_plane.application.metric_tiers import MetricTiers, cp_tiers, series_for
from axo_control_plane.application.notifications import Notifications
from axo_control_plane.application.people import Names, People
from axo_control_plane.application.ports import InMemoryHistory
from axo_control_plane.application.repair import VeRepair
from axo_control_plane.application.provisioner import Provisioner, StackSettings
from axo_control_plane.application.sdk_keys import SdkKeys
from axo_control_plane.application.settings import UserSettings
from axo_control_plane.application.sharing import Sharing
from axo_control_plane.application.sleeping import SleepManager
from axo_control_plane.application.statistics import Statistics, VeStatistics
from axo_control_plane.application.storage_metering import StorageMetering, month_name
from axo_control_plane.domain.periods import add_months
from axo_control_plane.application.teams import Teams
from axo_control_plane.application.ve_service import PurchaseRequest, VeService
from axo_control_plane.domain import virtual_environment as V
from axo_control_plane.domain.plans import Resources
from axo_control_plane.application.bucket_files import BucketFiles
from axo_control_plane.infrastructure.api.app import create_app
from fake_jobs import FakeFunctions
from fake_objects import FakeObjects
import fake_choreographies
from fake_choreographies import FakeChoreographies
from axo_control_plane.infrastructure.api.deps import Services
from axo_control_plane.server import ensure_indexes
from axo_control_plane.infrastructure.memory import (
    FakeStorage, FakeVem, InMemoryIdentity, InMemoryLeases, InMemoryStackDriver,
)

MINUTE = 60.0
HOUR = 3600.0
DAY = 86400.0

# Sample functions per VE: (id, name, typical execution ms, failure rate).
FUNCTIONS = {
    "ml-prod": [("fn-train-7c1e9a", "train-model", 4200, 0.04), ("fn-predict-2b44d0", "predict", 180, 0.01),
                ("fn-features-91fa3c", "build-features", 900, 0.02)],
    "etl": [("fn-ingest-55d201", "ingest-csv", 650, 0.03), ("fn-clean-a03b77", "clean-rows", 240, 0.05)],
    "analytics": [("fn-report-e8f412", "daily-report", 1500, 0.02)],
    "sandbox": [("fn-hello-0d9e11", "hello", 40, 0.0)],
}


def _png(width: int, height: int) -> bytes:
    """A small chart-like PNG (a falling curve), made without any image library."""
    import struct
    import zlib
    rows = bytearray()
    for y in range(height):
        rows.append(0)
        for x in range(width):
            curve = int(height * (0.15 + 0.7 * math.exp(-x / (width / 4))))
            on = abs(y - (height - curve)) < 3
            rows.extend((0, 134, 255) if on else (245, 247, 250) if y % 40 else (215, 220, 228))
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(bytes(rows))) + chunk(b"IEND", b""))


def _area_of(event_type: str) -> str:
    """The VEM's activity areas (axo_vem activity_repository.area_of)."""
    for prefix, area in (("Job", "job"), ("Function", "function"), ("ActiveObject", "object"),
                         ("Choreography", "choreography"), ("Endpoint", "endpoint"), ("Leader", "endpoint"),
                         ("Consensus", "endpoint"), ("Cluster", "endpoint")):
        if event_type.startswith(prefix):
            return area
    return "ve"


def _iso(t: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(t)) + f".{int(t % 1 * 1e6):06d}Z"


class FakeVemApi:
    """Answers the CP front door's pass-through calls (/ves/{id}/api/...) the
    way a VE's VEM would, with state per VE: settings, endpoints (a new one
    runs after a few seconds, a removed one drains first), history and
    policies. Grows with the UI: add a route when a page needs it."""

    START_S = 6.0          # creating / starting -> running
    DRAIN_S = 8.0          # draining -> removed

    def __init__(self, ve_doc: Callable[[str], Optional[Dict[str, Any]]], limits_of: Callable[[str], Dict[str, Any]],
                 lifecycle_of: Callable[[str], str]) -> None:
        self._ve_doc = ve_doc
        self._limits_of = limits_of
        self._lifecycle_of = lifecycle_of
        self._ves: Dict[str, Dict[str, Any]] = {}
        # re-entrant: the choreography engine's threads take it too (step jobs)
        self._lock = threading.RLock()
        # functions + jobs; ``report`` (an attempt -> the CP intake) is set by DevPlatform
        self.jobs = FakeFunctions(report=lambda ve_id, record: None, user_name=lambda u: u)
        self.objects = FakeObjects(self.jobs, self._history_page)
        # the REAL choreography code (axo_vem) on these functions / objects;
        # ``platform`` (buckets + storage) is set by DevPlatform
        self.choreographies = FakeChoreographies(self.jobs, self.objects, self._lock)
        self.choreographies.add_row = lambda ve_id, kind, meta, user: self._event(
            self._ve(ve_id), kind, None, meta, time.time(), user_id=user)

    # ── state ────────────────────────────────────────────────────────────────
    def _ve(self, ve_id: str) -> Dict[str, Any]:
        ve = self._ves.get(ve_id)
        if ve is None:
            doc = self._ve_doc(ve_id) or {}
            size = doc.get("endpoint_size") or {"cpu_cores": 1.0, "memory_mb": 1024}
            target = int(doc.get("target_endpoints") or 1)
            ve = self._ves[ve_id] = {
                "settings": {"target_endpoints": target, "min_endpoints": int(doc.get("min_endpoints") or 1),
                             "auto_replace_failed": True, "new_endpoint_cpu_cores": size["cpu_cores"],
                             "new_endpoint_memory_mb": size["memory_mb"], "endpoint_idle_seconds": None,
                             "wake_wait_seconds": 2.0, "default_max_wait_seconds": -1.0, "vem_queue_limit": None,
                             "endpoint_queue_limit": None, "runner_queue_limit": None, "retention_days": None,
                             "result_keep_max": "never", "result_keep_default": "7d"},
                "endpoints": {}, "history": [], "policies": [], "next": 0,
            }
            started = time.time() - 3 * DAY
            for i in range(max(target, 2)):
                ep = self._add_endpoint(ve, ve_id, size["cpu_cores"], size["memory_mb"], "the VE was created",
                                        at=started + i * HOUR, state="running")
                if i >= target:     # the sample metrics name two endpoints: the second one was removed
                    self._set_state(ve, ep, "removed", "scaled down", at=started + DAY)
            self._elect(ve)
            # A busy recent past, so the Activity page has pages to load.
            for fid, fname, ms, fail in FUNCTIONS.get(doc.get("name") or "", []):
                for k in range(120):
                    at = time.time() - k * 600 - hash(fid) % 300
                    kind = "JobFailed" if (k * 7919) % 100 < fail * 100 else "JobCompleted"
                    self._event(ve, kind, f"{ve_id}-ep-0",
                                {"function_id": fid, "name": fname, "job_id": uuid.uuid4().hex, "duration_ms": ms}, at)
        self._advance(ve)
        return ve

    def _add_endpoint(self, ve, ve_id, cores, mem, reason, at=None, state="creating"):
        at = time.time() if at is None else at
        eid = f"{ve_id}-ep-{ve['next']}"
        ve["next"] += 1
        ve["endpoints"][eid] = {"endpoint_id": eid, "state": state, "since": at, "reason": reason,
                                "cpu_cores": cores, "memory_mb": mem, "failures": 0, "started_at": at,
                                "completed": 0, "failed": 0}
        self._event(ve, "EndpointLifecycleChanged", eid, {"state": state, "reason": reason}, at)
        if state == "running":
            self._event(ve, "EndpointStarted", eid, {}, at)
        return ve["endpoints"][eid]

    def _set_state(self, ve, ep, state, reason, at=None):
        at = time.time() if at is None else at
        ep.update(state=state, since=at, reason=reason)
        self._event(ve, "EndpointLifecycleChanged", ep["endpoint_id"], {"state": state, "reason": reason}, at)
        if state == "running":
            ep["started_at"] = at
            self._event(ve, "EndpointStarted", ep["endpoint_id"], {}, at)
        elif state == "removed":
            self._event(ve, "EndpointStopped", ep["endpoint_id"], {}, at)

    def _event(self, ve, kind, eid, meta, at, user_id=None):
        ve["history"].append({"event_id": uuid.uuid4().hex, "created_at": _iso(at), "endpoint_id": eid,
                              "user_id": user_id, "event_type": kind, "area": _area_of(kind), "meta": meta})

    @staticmethod
    def _history_page(rows, q) -> List[Dict[str, Any]]:
        """Like the VEM's history: newest first, ``areas``, and the page after
        ``before`` / ``before_id`` (the last row shown)."""
        rows = sorted(rows, key=lambda r: (r["created_at"], r["event_id"]), reverse=True)
        if q.get("areas"):
            rows = [r for r in rows if r["area"] in q["areas"].split(",")]
        if q.get("since_minutes"):
            rows = [r for r in rows if r["created_at"] >= _iso(time.time() - 60 * int(q["since_minutes"]))]
        if q.get("before"):
            before = q["before"].replace("+00:00", "Z")
            last = (before, q.get("before_id") or "")
            rows = [r for r in rows if (r["created_at"], r["event_id"]) < last]
        return rows[: int(q.get("limit", 100))]

    def _elect(self, ve) -> None:
        running = sorted(e["endpoint_id"] for e in ve["endpoints"].values() if e["state"] == "running")
        leader = running[-1] if running else None
        if leader and leader != ve.get("leader"):
            ve["leader"] = leader
            ve["term"] = ve.get("term", -1) + 1
            self._event(ve, "LeaderElected", leader, {"leader_id": leader, "term": ve["term"]}, time.time())

    def _advance(self, ve) -> None:
        """Moves endpoints on with time, as the real reconciler would."""
        now = time.time()
        for ep in list(ve["endpoints"].values()):
            if ep["state"] in ("creating", "starting") and now - ep["since"] >= self.START_S:
                self._set_state(ve, ep, "running", "reported in", at=ep["since"] + self.START_S)
            elif ep["state"] == "draining" and now - ep["since"] >= self.DRAIN_S:
                self._set_state(ve, ep, "removed", ep["reason"], at=ep["since"] + self.DRAIN_S)
            elif ep["state"] == "running":
                # some work, so the counters move
                ep["completed"] += random.randrange(0, 3)
                ep["failed"] += 1 if random.random() < 0.03 else 0
        self._elect(ve)

    def target(self, ve_id: str) -> int:
        with self._lock:
            return int(self._ve(ve_id)["settings"]["target_endpoints"])

    def running(self, ve_id: str) -> int:
        with self._lock:
            return sum(1 for e in self._ve(ve_id)["endpoints"].values() if e["state"] == "running")

    def _present(self, ve) -> List[Dict[str, Any]]:
        return [e for e in ve["endpoints"].values() if e["state"] in ("creating", "starting", "running")]

    def _view(self, ve_id: str, ep: Dict[str, Any], ve) -> Dict[str, Any]:
        now = time.time()
        reports = ep["state"] in ("running", "draining")
        out = {
            "endpoint_id": ep["endpoint_id"], "lifecycle": ep["state"], "lifecycle_since": _iso(ep["since"]),
            "lifecycle_reason": ep["reason"], "cpu_cores": ep["cpu_cores"], "memory_mb": ep["memory_mb"],
            "failures": ep["failures"],
        }
        if ep["state"] in ("creating", "starting"):
            return out
        leader = ve.get("leader")
        out.update({
            "created_at": _iso(ep["started_at"]), "virtual_environment_id": ve_id,
            "router_bind": "tcp://0.0.0.0:5555", "pub_bind": "tcp://0.0.0.0:5556",
            "status": "running" if reports else "stopped", "runtime_type": None, "user_id": None,
            "uptime_ms": (now - ep["started_at"]) * 1000 if reports else None,
            "purge_eligible": not reports, "unreachable_since": None,
            "metrics": {
                "submitted": ep["completed"] + ep["failed"], "completed": ep["completed"], "failed": ep["failed"],
                "rejected": 0, "queue_depth": random.randrange(0, 4) if reports else 0, "queue_capacity": 1000,
                "consensus_role": "leader" if ep["endpoint_id"] == leader else "follower",
                "consensus_term": ve.get("term", 0), "consensus_leader_ids": [leader] if leader else [],
                "consensus_dirty_pending": 0, "draining": ep["state"] == "draining",
                "container_pools": ({"fn-train-7c1e9a::1": {"live": 2, "idle": 1}, "fn-predict-2b44d0::1": {"live": 1, "idle": 1}}
                                    if ep["endpoint_id"] == leader else {}),
            },
        })
        return out

    def _ve_view(self, ve_id: str, ve) -> Dict[str, Any]:
        s = ve["settings"]
        limits = self._limits_of(ve_id)
        running = sum(1 for e in ve["endpoints"].values() if e["state"] == "running")
        return {"virtual_environment_id": ve_id, "plan_limits": {
                    "max_queue_limit": None, "max_wait_seconds": None, "allow_wait_forever": True,
                    "history_grace_days": 7, **limits},
                "settings": dict(s), "effective_retention_days": s["retention_days"] or limits.get("max_retention_days"),
                "lifecycle": self._lifecycle_of(ve_id), "health": "Healthy" if running >= s["min_endpoints"] else "Degraded",
                "target_endpoints": s["target_endpoints"], "min_endpoints": s["min_endpoints"],
                "endpoints": {"running": running}, "errors": []}

    # ── routes ───────────────────────────────────────────────────────────────
    def __call__(self, request: httpx.Request) -> httpx.Response:
        host = request.url.host                          # ve-<id>-vem
        ve_id = host[3:-4] if host.startswith("ve-") and host.endswith("-vem") else ""
        path = request.url.path
        if fake_choreographies.handles(path) and not path.endswith("/history"):
            # outside the lock: a run's start submits step jobs, which take it
            with self._lock:
                fve, ove, owner = self._fake_ves(ve_id)
                bridge = self.choreographies.ve(ve_id, fve, ove, owner)
            return self.choreographies.route(bridge, request)
        with self._lock:
            return self._route(request, ve_id)

    def _fake_ves(self, ve_id: str):
        """The VE's fake functions + jobs and its objects (made on first use)."""
        ve, doc = self._ve(ve_id), self._ve_doc(ve_id) or {}
        running = sorted(e["endpoint_id"] for e in ve["endpoints"].values() if e["state"] == "running")
        owner = (doc.get("owner") or {}).get("id") or "demo"
        fve = self.jobs._ve(ve_id, FUNCTIONS.get(doc.get("name") or "", []), running, owner)
        return fve, self.objects._ve(ve_id, fve), owner

    def _route(self, request: httpx.Request, ve_id: str) -> httpx.Response:
        method, path, q = request.method, request.url.path, request.url.params
        try:
            body = json.loads(request.content or b"{}") if request.content else {}
        except (ValueError, UnicodeDecodeError):
            body = {}                                  # a form upload (POST /functions)
        who = request.headers.get("X-Acting-User", "")
        ve = self._ve(ve_id)
        doc = self._ve_doc(ve_id) or {}
        ok = lambda data, code=200: httpx.Response(code, json=data)   # noqa: E731
        err = lambda code, name, num, msg: httpx.Response(code, json={   # noqa: E731
            "error": name, "error_code": num, "message": msg, "details": {}})
        ep_match = re.match(r"^/endpoints/([^/]+)(/restart|/purge|/history)?$", path)
        eps = ve["endpoints"]

        if method == "GET" and path == "/ve":
            return ok(self._ve_view(ve_id, ve))
        if method == "PATCH" and path == "/ve/settings":
            changes = {k: v for k, v in body.items() if k in ve["settings"]}
            ve["settings"].update(changes)
            self._scale_to(ve, ve_id, ve["settings"]["target_endpoints"], who, body.get("reason") or "settings changed")
            self._event(ve, "VirtualEnvironmentSettingsChanged", None, {"changes": changes}, time.time(), user_id=who)
            return ok(self._ve_view(ve_id, ve))
        if method == "GET" and path == "/policies":
            return ok({"allowed": bool(self._limits_of(ve_id).get("policies_allowed")), "paused_until": None,
                       "pause_reason": None, "policies": ve["policies"]})
        if method == "POST" and path == "/policies":
            policy = {**body, "policy_id": uuid.uuid4().hex[:12], "created_at": _iso(time.time())}
            ve["policies"].append(policy)
            return ok(policy, 201)
        if method == "DELETE" and path.startswith("/policies/"):
            ve["policies"] = [p for p in ve["policies"] if p["policy_id"] != path.split("/")[2]]
            return ok({"deleted": True})

        if method == "GET" and path == "/endpoints":
            shown = [e for e in eps.values() if q.get("include_removed") == "true" or e["state"] != "removed"]
            return ok([self._view(ve_id, e, ve) for e in shown])
        if method == "POST" and path == "/endpoints":
            count = int(body.get("count") or 1)
            target = ve["settings"]["target_endpoints"] + count
            limit = self._limits_of(ve_id).get("max_endpoints") or 0
            if target > limit:
                return err(409, "VEM_CONFLICT", 9603, f"target_endpoints {target} is above the plan's maximum ({limit})")
            ve["settings"]["target_endpoints"] = target
            made = [self._add_endpoint(ve, ve_id, body.get("cpu_cores") or ve["settings"]["new_endpoint_cpu_cores"],
                                       body.get("memory_mb") or ve["settings"]["new_endpoint_memory_mb"],
                                       body.get("reason") or f"added by {who}") for _ in range(count)]
            return ok([self._view(ve_id, e, ve) for e in made], 201)
        if ep_match:
            ep = eps.get(ep_match.group(1))
            action = ep_match.group(2)
            if ep is None:
                return err(404, "VEM_NOT_FOUND", 9601, "endpoint not found")
            if method == "GET" and action is None:
                return ok(self._view(ve_id, ep, ve))
            if method == "GET" and action == "/history":
                return ok(self._history_page([r for r in ve["history"] if r.get("endpoint_id") == ep["endpoint_id"]], q))
            if method == "POST" and action == "/restart":
                if ep["state"] != "running":
                    return err(409, "VEM_CONFLICT", 9603, "restart failed: the endpoint isn't running")
                self._set_state(ve, ep, "starting", f"restarted by {who}")
                return ok(self._view(ve_id, ep, ve))
            if method == "DELETE" and action is None:
                if ep["state"] not in ("creating", "starting", "running"):
                    return ok(self._view(ve_id, ep, ve))
                if len(self._present(ve)) - 1 < ve["settings"]["min_endpoints"]:
                    return err(409, "VEM_CONFLICT", 9603,
                               f"can't go below min_endpoints ({ve['settings']['min_endpoints']})")
                ve["settings"]["target_endpoints"] -= 1
                self._set_state(ve, ep, "draining", q.get("reason") or f"removed by {who}")
                return ok(self._view(ve_id, ep, ve))
            if method == "DELETE" and action == "/purge":
                if ep["state"] != "removed":
                    return err(409, "VEM_CONFLICT", 9603, "endpoint must be stopped or unreachable before it can be purged")
                del eps[ep["endpoint_id"]]          # its history stays (the VE's retention deletes it)
                return ok({"endpoint_id": ep["endpoint_id"]})
        if method == "GET" and path == "/consensus/leader":
            if not ve.get("leader"):
                return err(404, "VEM_NOT_FOUND", 9601, "no consensus view reported yet")
            return ok({"endpoint_id": ve["leader"], "leader_ids": [ve["leader"]], "term": ve.get("term", 0),
                       "reported_by": [e["endpoint_id"] for e in self._present(ve)]})
        if method == "GET" and path == "/history":
            rows = ve["history"]
            if q.get("endpoint_id"):
                rows = [r for r in rows if r.get("endpoint_id") == q["endpoint_id"]]
            return ok(self._history_page(rows, q))

        running = sorted(e["endpoint_id"] for e in eps.values() if e["state"] == "running")
        ch_history = re.match(r"^/choreographies/([^/]+)/history$", path)
        if method == "GET" and ch_history:
            return ok(self._history_page([r for r in ve["history"]
                                          if (r.get("meta") or {}).get("choreography_id") == ch_history.group(1)], q))
        if path.startswith("/jobs"):
            self.choreographies.sync()             # a run's step jobs group under its summary
        fn_history = re.match(r"^/functions/([^/]+)/history$", path)
        if method == "GET" and fn_history:
            return ok(self._history_page(self.jobs.function_history(
                ve_id, FUNCTIONS.get(doc.get("name") or "", []), running, (doc.get("owner") or {}).get("id") or "demo",
                fn_history.group(1)), q))
        owner = (doc.get("owner") or {}).get("id") or "demo"
        fve = self.jobs._ve(ve_id, FUNCTIONS.get(doc.get("name") or "", []), running, owner)
        answer = self.objects.route(ve_id, fve, self._limits_of(ve_id), request, body, who,
                                    request.headers.get("X-Acting-Role", ""), ok, err)
        if answer is not None:
            return answer
        answer = self.jobs.route(ve_id, FUNCTIONS.get(doc.get("name") or "", []), running,
                                 (doc.get("owner") or {}).get("id") or "demo", request, body, who,
                                 request.headers.get("X-Acting-Role", ""), ok, err)
        if answer is not None:
            return answer
        return err(404, "VEM_NOT_FOUND", 9601, f"the dev VEM has no {method} {path} yet")

    def _scale_to(self, ve, ve_id, target, who, reason) -> None:
        present = self._present(ve)
        s = ve["settings"]
        for _ in range(target - len(present)):
            self._add_endpoint(ve, ve_id, s["new_endpoint_cpu_cores"], s["new_endpoint_memory_mb"], reason)
        for ep in sorted(present, key=lambda e: e["since"], reverse=True)[: max(0, len(present) - target)]:
            self._set_state(ve, ep, "draining", reason)


class DevPlatform:
    def __init__(self, seed_days: int = 30, mongo_uri: str = "") -> None:
        if mongo_uri:
            import pymongo
            client = pymongo.MongoClient(mongo_uri)
            client.drop_database("axo_cp_dev")              # a fresh sample every start
            self.db = db = client["axo_cp_dev"]
        else:
            self.db = db = mongomock.MongoClient()["axo_cp_dev"]
        ensure_indexes(db)                                  # as the real server does
        # mongomock scans whole collections on every update: rolling a month of
        # minutes into 15-min / day bins takes minutes there, so only do it on
        # a real MongoDB (with mongomock, windows up to 7 days have data).
        self.rollups = bool(mongo_uri)
        self.history = history = InMemoryHistory()
        self.identity = identity = InMemoryIdentity()
        invalidations = Invalidations(db)
        self.access = access = AccessResolver(identity, invalidations, ttl_s=300.0)
        self.authz = authz = Authorizer(access)
        names = Names(db)
        self.people = people = People(db, identity, access, names, history)
        people.bootstrap_admin("admin", "admin-password", "admin@axo.local")
        self.teams = teams = Teams(db, identity, access, names, history)
        self.settings = UserSettings(db)
        self.notifications = notifications = Notifications(db, settings=self.settings)
        self.catalog = catalog = PlanCatalog(db, history)
        catalog.seed()
        capacity = CapacityLedger(db, Resources(64.0, 131072))
        self.ves = ves = VeService(db, catalog, capacity, identity, access, authz, teams, notifications, history,
                                   max_custom=Resources(16.0, 32768))
        self.vem = vem = FakeVem()
        leases = InMemoryLeases()
        settings = StackSettings(
            vem_image="axo-vem:dev", endpoint_image="axo-endpoint:dev", kurrent_image="kurrent:dev",
            mongo_image="mongo:dev", control_network="axo-control-net", storage_container="axo-storage",
            storage_uri="tcp://axo-storage:7000", cp_intake_uri="tcp://cp:9100",
            vem_token_fn=lambda ve_id: f"vem-token-{ve_id}", storage_token_fn=lambda ve_id: f"st-{ve_id}",
        )
        driver = InMemoryStackDriver()
        self.provisioner = Provisioner(db, driver, vem, leases, capacity, catalog, identity,
                                       notifications, history, settings, instance_id="cp-dev")
        # Repair against the fakes: the sample VEs have no containers, so the
        # first repair "creates" them.
        ves.repairs = VeRepair(db, driver, vem, self.provisioner, notifications, history, "axo-storage",
                               vem_wait_s=2.0, poll_s=0.5)
        self.storage = storage = FakeStorage()
        self.buckets = buckets = Buckets(db, storage, identity, access, authz, names, teams, history,
                          default_quota_bytes=10 * 1024 ** 3, max_quota_bytes=1024 ** 4)
        self.sharing = sharing = Sharing(db, people, names, teams, buckets, identity, access, authz, notifications,
                                         history)
        keys = SdkKeys(db, people, authz, history, pepper="dev-pepper")
        self.storage_metering = StorageMetering(db, catalog, history)
        self.intake = VemIntake(db, history, notifications, catalog, storage=self.storage_metering)
        tiers = cp_tiers()
        self.tier_list = tiers
        self.tiers = MetricTiers(db, tiers, series_for(tiers))
        figures = Statistics(db, catalog, tiers)
        self.bills = VeBills(db, history)
        sleep = SleepManager(db, catalog, vem, leases, notifications, history, instance_id="cp-dev")
        self.services = Services(
            people=people, teams=teams, ves=ves, buckets=buckets, sharing=sharing, keys=keys,
            notifications=notifications, catalog=catalog, capacity=capacity,
            front_door=FrontDoor(db, authz, sleep, catalog, notifications), sleep=sleep,
            storage_access=StorageAccess(buckets, access), cleanup=None,
            vem_base_url=lambda ve_id: f"http://ve-{ve_id}-vem:8080",
            vem_headers=lambda ve_id, user: {"Authorization": f"Bearer vem-token-{ve_id}", "X-Acting-User": user},
            vem_ws_url=lambda ve_id, path: f"ws://ve-{ve_id}-vem:8080{path}?token=dev",
            object_store=storage, storage_check_token="dev-storage-secret", db=db,
            bucket_files=BucketFiles(db, buckets, storage, people),
            statistics=VeStatistics(figures, authz, list_fn=ves.list_for),
            billing=BillingViews(db, authz, teams, ves, self.bills, self.storage_metering, figures,
                                 bucket_stats=storage.bucket_stats),
            settings=self.settings,
        )
        self.vem_api = FakeVemApi(lambda ve_id: self.db["virtual_environments"].find_one({"_id": ve_id}),
                                  self._limits,
                                  lambda ve_id: self.vem.state.get(ve_id, {}).get("lifecycle", "Running"))
        self.vem_api.choreographies.platform = self
        self.vem_api.jobs._report = lambda ve_id, record: self.intake.handle(
            "JobAttemptRecorded", f"vem:{ve_id}", {**record, "virtual_environment_id": ve_id})
        self.rng = random.Random(7)
        self._seed(seed_days)

    # ── sample data ──────────────────────────────────────────────────────────
    def _user(self, username: str, password: str, first: str, last: str):
        self.people.signup(username, f"{username}@example.com", password, first, last)
        session = self.people.login(username, password)
        return self.people.actor_for(self.people.verify(session["access_token"], session["temporal_secret"]))

    def _seed(self, days: int) -> None:
        demo = self._user("demo", "demo-password", "Dana", "Demo")
        ana = self._user("ana", "ana-password", "Ana", "Lopez")
        made = [
            self.ves.buy(demo, PurchaseRequest(name="ml-prod", plan_id="standard", quota_cpu_cores=8.0,
                                               quota_memory_mb=16384)),
            self.ves.buy(demo, PurchaseRequest(name="etl", plan_id="basic", quota_cpu_cores=4.0, quota_memory_mb=8192)),
            self.ves.buy(demo, PurchaseRequest(name="sandbox", plan_id="free", quota_cpu_cores=1.0,
                                               quota_memory_mb=2048)),
            self.ves.buy(ana, PurchaseRequest(name="analytics", plan_id="basic", quota_cpu_cores=4.0,
                                              quota_memory_mb=8192)),
        ]
        self.ve_ids = {r["ve"]["name"]: r["ve"]["ve_id"] for r in made}
        self._seed_files(demo, ana)
        names = {demo.user_id: "Dana Demo", ana.user_id: "Ana Lopez"}
        self.vem_api.jobs._name_of = lambda user: names.get(user, user)
        self.vem_api.jobs.people = {"owner": demo.user_id, "other": ana.user_id}
        invitation = self.sharing.invite_to_ve(ana, self.ve_ids["analytics"], "demo", "developer")
        self.sharing.answer(demo, invitation["id"], accept=True)
        self._seed_teams(demo, ana)
        # Bring them up (provisioner + VEM heartbeats), then put the Free one to sleep.
        for _ in range(4):
            self.heartbeats()
            self.provisioner.tick()
        self.db["virtual_environments"].update_one({"_id": self.ve_ids["sandbox"]},
                                                   {"$set": {"desired": V.DESIRED_SLEEPING}})
        self.vem.state.setdefault(self.ve_ids["sandbox"], {})["lifecycle"] = "Sleeping"
        # Rentals started `days` ago so there is history; then records.
        now = time.time()
        self.db["virtual_environments"].update_many({}, {"$set": {"created_at": now - days * DAY}})
        # The current rental period started 10 days ago, so the open bills
        # already hold some usage (fee lines moved with it).
        shift = -min(10.0, days) * DAY
        self.db["virtual_environments"].update_many({}, {"$inc": {"period_anchor": shift, "period_start": shift,
                                                                 "period_end": shift}})
        self.db["ve_charges"].update_many({}, {"$inc": {"period_start": shift, "period_end": shift, "at": shift}})
        for name, ve_id in self.ve_ids.items():
            if name == "sandbox":
                continue
            self._seed_bins(name, ve_id, now - days * DAY, now)
        if self.rollups:
            # Merge the old bins while they're "on time" (as if the rollups
            # had been running all along), then the real pass expires them.
            MetricTiers(self.db, self.tier_list, series_for(self.tier_list), now_fn=lambda: now - days * DAY).tick()
            self.tiers.tick()
        self._seed_past_bills(demo, now)
        self._seed_notifications(demo, now)
        self.services.keys.create(demo, "laptop-dana", None, "developer", 90)
        self.services.keys.create(demo, "ci-ml-prod", self.ve_ids["ml-prod"], "viewer", 30)
        self.db["sdk_keys"].update_many({}, {"$set": {"created_at": now - 200 * DAY}})
        self.db["sdk_keys"].update_one({"name": "ci-ml-prod"}, {"$set": {"last_used_at": now - 3600,
                                                                          "expires_at": now + 12 * DAY}})

    def _seed_past_bills(self, demo, now: float) -> None:
        """Closed bills to list on the Billing page: two past periods per
        paid VE of demo, one of a VE since deleted, two storage months."""
        def past(bill: Dict[str, Any], back: int, ve_id: str, name: str) -> Dict[str, Any]:
            length = bill["period_end"] - bill["period_start"]
            scale = 0.6 + 0.3 * back
            usage_cents = int(bill["usage_cents"] * scale) + 37 * back
            return {**bill, "_id": f"{ve_id}:{bill['period_index'] - back}", "ve_id": ve_id, "ve_name": name,
                    "period_index": bill["period_index"] - back, "status": "closed",
                    "period_start": bill["period_start"] - back * length,
                    "period_end": bill["period_end"] - back * length,
                    "closed_at": bill["period_end"] - back * length + 3600,
                    "fees": [{**f, "at": f["at"] - back * length} for f in bill["fees"]], "late_usage": [],
                    "usage_cents": usage_cents, "total_cents": bill["fees_cents"] + usage_cents}
        for name in ("ml-prod", "etl"):
            ve_id = self.ve_ids[name]
            current = self.bills.current(ve_id)
            for back in (1, 2):
                self.db["ve_bills"].insert_one(past(current, back, ve_id, name))
        late = past(self.bills.current(self.ve_ids["etl"]), 3, "ve-old-tests", "old-tests")
        self.db["ve_bills"].insert_one({**late, "late_usage": [{**late["usage"], "from_period_index": 0,
                                                                "from_period_start": late["period_start"],
                                                                "from_period_end": late["period_end"]}]})
        storage = self.storage_metering.current("user", demo.user_id)
        start = storage["period_start"]
        for back in (1, 2):
            begin = add_months(start, -back)
            month = month_name(begin)
            self.db["storage_bills"].insert_one({
                **storage, "_id": f"storage:user:{demo.user_id}:{month}", "month": month, "status": "closed",
                "period_start": begin, "period_end": add_months(begin, 1), "closed_at": add_months(begin, 1) + 3600,
                "total_ucents": storage["total_ucents"] + back * 41_000_000,
                "total_cents": storage["total_cents"] + back * 41})

    def _seed_notifications(self, demo, now: float) -> None:
        """A month of notices of every group, so the notifications page has
        days, filters and more than one page."""
        ml, etl = self.ve_ids["ml-prod"], self.ve_ids["etl"]
        samples = [
            ("ve_state", "VE ml-prod is ready", {"ve_id": ml}),
            ("budget_warning", "etl used 80 % of its $10.00 budget", {"ve_id": etl, "threshold": 80}),
            ("job_finished", "Job train-model finished on ml-prod", {"ve_id": ml}),
            ("bill_closed", "The bill of ml-prod for the last period is ready", {"ve_id": ml}),
            ("renewal_soon", "etl renews in 3 days", {"ve_id": etl}),
            ("ve_state", "VE etl stopped", {"ve_id": etl}),
            ("invitation_answered", "Ana accepted your invitation to data-team", {}),
            ("limit_warning", "sandbox used 80 % of its Free requests", {"ve_id": self.ve_ids["sandbox"]}),
        ]
        known = {d["_id"] for d in self.db["notifications"].find({"user_id": demo.user_id}, {"_id": 1})}
        for i in range(60):
            kind, text, data = samples[i % len(samples)]
            self.notifications.notify([demo.user_id], kind, text, data)
        docs = [d for d in self.db["notifications"].find({"user_id": demo.user_id}).sort("created_at", 1)
                if d["_id"] not in known]
        for i, doc in enumerate(docs):
            age = (len(docs) - 1 - i) * 17 * 3600.0           # every 17 hours, newest now
            self.db["notifications"].update_one({"_id": doc["_id"]}, {"$set": {"created_at": now - age,
                                                                             "read": age > 3 * DAY}})

    def _seed_teams(self, demo, ana) -> None:
        """data-team (demo owns it, Ana is Admin, Luis a Member) with a bucket
        of its own and Ana's VE shared with it; and, waiting in demo's inbox,
        Ana's invitation to research and her offer to give demo analytics."""
        luis = self._user("luis", "luis-password", "Luis", "Ruiz")
        team = self.teams.create(demo, "data-team", "Sales analytics and reports")
        for who, role in ((ana, "admin"), (luis, "member")):
            invite = self.sharing.invite_to_team(demo, team["team_id"], who.username, role)
            self.sharing.answer(who, invite["id"], accept=True)
        self.buckets.create(demo, "reports", owner_name="data-team")
        shared = self.sharing.invite_to_ve(ana, self.ve_ids["analytics"], "data-team", "viewer")
        self.sharing.answer(demo, shared["id"], accept=True)
        research = self.teams.create(ana, "research", "Model experiments")
        self.sharing.invite_to_team(ana, research["team_id"], "demo", "member")
        self.sharing.request_transfer(ana, "ve", self.ve_ids["analytics"], "demo")
        self.sharing.invite_to_ve(demo, self.ve_ids["ml-prod"], "luis", "viewer")

    def _seed_files(self, demo, ana) -> None:
        """The buckets the sample jobs read and write, with folders, versions,
        a file a job wrote, one shared by Ana and one deleted (restorable)."""
        store = self.services.object_store
        def rows(month: int) -> bytes:
            return b"day,visits,orders\n" + b"".join(
                f"2026-{month:02d}-{d:02d},{900 + d * 13},{40 + d}\n".encode() for d in range(1, 21))
        buckets = {}
        for owner, bucket, files in (
            (demo, "ml-data", {"2026-10.csv": rows(10), "raw/2026-08.csv": rows(8), "raw/2026-09.csv": rows(9),
                               "raw/archive/2025-12.csv": rows(12), "README.md": b"# ml-data\n\nDaily visits.\n"}),
            (demo, "ml-models", {"model.json": json.dumps({"kind": "logistic", "weights": [0.4, -1.2, 2.2],
                                                           "loss": 0.071}, indent=2).encode(),
                                 "loss-curve.png": _png(480, 240), "notes.txt": b"Trained on October's rows.\n",
                                 "weights.bin": bytes(range(256)) * 64}),
            (ana, "survey", {"answers.csv": b"q,a\n1,yes\n2,no\n", "summary.json": b'{"yes": 1, "no": 1}'}),
            (demo, "old-exports", {"export.csv": rows(1)}),
        ):
            doc = self.services.buckets.create(owner, bucket)
            buckets[bucket] = doc
            for name, data in files.items():
                store.upload(owner.user_id, doc["bucket_id"], name, data)
        # Versions, and a file a job wrote (its attempt says which function).
        key = buckets["ml-models"]["bucket_id"]
        store.upload(demo.user_id, key, "model.json", json.dumps({"kind": "logistic", "weights": [0.5, -1.1, 2.0],
                                                                 "loss": 0.064}, indent=2).encode())
        ve = self.ve_ids["ml-prod"]
        newest = store.files[(key, "model.json")]
        newest[max(newest)]["source"] = f"job:{ve}:job-train-1"
        self.db["job_attempts"].insert_one({"_id": f"{ve}:job-train-1:1", "job_id": "job-train-1", "attempt": 1,
                                            "ve_id": ve, "function_id": "fn-train-7c1e9a", "function_version": 1,
                                            "finished_at_ts": time.time()})
        # Two days of what each VE keeps: its results bucket and object state.
        hour = int(time.time() // 3600 * 3600)
        for n, (name, ve_id) in enumerate(self.ve_ids.items()):
            for h in range(48):
                ts = hour - h * 3600
                results = (120 + 40 * n) * 1024 ** 2 - h * 900_000
                state = (8 + 3 * n) * 1024 ** 2 - h * 40_000
                owner = self.db["virtual_environments"].find_one({"_id": ve_id})["owner"]
                payer = {k: owner.get(k) for k in ("kind", "id", "name")}
                self.db["storage_size_hours"].insert_one({
                    "_id": f"dev:{ts}:{ve_id}", "ts": ts, "payer": payer, "payer_key": f"{payer['kind']}:{payer['id']}",
                    "bucket_id": f"axo/ve-{ve_id}-results", "bytes": results, "gb_months": results / 1024 ** 3 / 730,
                    "cost": {"total_ucents": round(results / 1024 ** 3 / 730 * 2_300_000)}, "bill_id": None,
                    "received_at": ts})
                self.db["object_state_hours"].insert_one({
                    "_id": f"{ve_id}:{ts}", "ve_id": ve_id, "ts": ts, "bytes": state, "gb_months": state / 1024 ** 3 / 730,
                    "cost": {"total_ucents": round(state / 1024 ** 3 / 730 * 2_300_000)}, "bill_id": None,
                    "received_by_cp_at": ts})
        invite = self.sharing.invite_to_bucket(ana, "ana/survey", "demo", "reader")
        self.sharing.answer(demo, invite["id"], accept=True)
        self.services.buckets.delete(demo, "demo/old-exports")

    def _load(self, name: str, t: float) -> float:
        """Jobs per hour: busy on weekday office hours, quiet at night."""
        lt = time.localtime(t)
        base = {"ml-prod": 40, "etl": 18, "analytics": 8}.get(name, 4)
        office = 1.0 if lt.tm_wday < 5 and 8 <= lt.tm_hour <= 19 else 0.25
        wave = 0.75 + 0.25 * math.sin(lt.tm_hour / 24 * 2 * math.pi)
        return base * office * wave

    def _attempt(self, name: str, ve_id: str, finished: float) -> Tuple[str, str, str, float]:
        """One job attempt record; returns (function, endpoint, outcome, total ms) for the bin."""
        rng = self.rng
        fid, _, exec_ms, fail = rng.choice(FUNCTIONS[name])
        r = rng.random()
        outcome = ("failed_user" if r < fail else "failed_platform" if r < fail * 1.3
                   else "timed_out_in_queue" if r < fail * 1.4 else "completed")
        execution = max(5.0, rng.lognormvariate(math.log(exec_ms), 0.45))
        waits = {"control_plane": rng.uniform(1, 6), "vem": rng.uniform(2, 15), "endpoint": rng.uniform(1, 40)}
        bucket = f"demo/{name}-data"
        endpoint = f"{ve_id}-ep-{rng.randrange(2)}"
        self.intake.handle("JobAttemptRecorded", f"vem:{ve_id}", {
            "virtual_environment_id": ve_id, "job_id": str(uuid.uuid4()), "attempt": 1,
            "trace_id": uuid.uuid4().hex, "outcome": outcome, "owner_type": "user", "owner_id": "demo",
            "started_by_type": "user" if rng.random() < 0.6 else "api_key",
            "started_by_id": rng.choice(["demo", "ana"]) if rng.random() < 0.6 else "key-ci-runner",
            "function_id": fid, "function_version": 1, "endpoint_cores": 1.0, "endpoint_ram_mb": 2048,
            "endpoint_id": endpoint,
            "received_at": finished - (execution + sum(waits.values())) / 1000, "finished_at": finished,
            "execution_ms": execution, "layer_waits_ms": waits,
            "bytes_read": {bucket: rng.randrange(10_000, 5_000_000)}, "bytes_written": {bucket: rng.randrange(0, 800_000)},
            "cache_hits": rng.randrange(0, 4), "cache_misses": rng.randrange(0, 2)})
        return fid, endpoint, outcome, execution + sum(waits.values())

    def _bin(self, name: str, ve_id: str, start: float, width: float) -> None:
        """The jobs of ``start``..``start + width`` and the VEM summary made
        from those same jobs, so statistics and job records agree."""
        rng = self.rng
        jobs = max(0, round(rng.gauss(self._load(name, start) * width / HOUR, 0.5)))
        times: Dict[Tuple[str, str, str], StatSummary] = {}
        for _ in range(jobs):
            fid, endpoint, outcome, total_ms = self._attempt(name, ve_id, start + rng.uniform(0, width))
            times.setdefault((fid, endpoint, outcome), StatSummary()).add(total_ms)
        summaries = [{"metric": m.JOB_TOTAL_MS, "dims": {m.DIM_FUNCTION: fid, m.DIM_FUNCTION_VERSION: "1",
                                                         m.DIM_ENDPOINT: endpoint, m.DIM_OUTCOME: outcome},
                      "stats": stats.to_dict()} for (fid, endpoint, outcome), stats in times.items()]
        for ep in range(2):
            for metric, mean in ((m.RESOURCE_CPU_PCT, 35 + 25 * math.sin(start / 5000 + ep)),
                                 (m.RESOURCE_RAM_PCT, 55 + 10 * math.sin(start / 9000 + ep))):
                s = StatSummary()
                for _ in range(6):
                    s.add(max(0.0, min(100.0, rng.gauss(mean, 6))))
                summaries.append({"metric": metric, "dims": {m.DIM_ENDPOINT: f"{ve_id}-ep-{ep}"}, "stats": s.to_dict()})
        for ep in range(2):
            requests = StatSummary()
            requests.add(sum(t.count for (_, endpoint, _), t in times.items() if endpoint == f"{ve_id}-ep-{ep}"))
            summaries.append({"metric": m.REQUESTS_RECEIVED, "dims": {m.DIM_LAYER: m.LAYER_ENDPOINT,
                                                                      m.DIM_ENDPOINT: f"{ve_id}-ep-{ep}"},
                              "stats": requests.to_dict()})
            queue = StatSummary()
            for _ in range(6):
                queue.add(max(0.0, rng.gauss(jobs / 20, 1)))
            summaries.append({"metric": m.QUEUE_LENGTH, "dims": {m.DIM_LAYER: m.LAYER_ENDPOINT, m.DIM_QUEUE: "dispatcher",
                                                                 m.DIM_ENDPOINT: f"{ve_id}-ep-{ep}"},
                              "stats": queue.to_dict()})
        self.intake.handle("MetricSummaryReported", f"vem:{ve_id}", {
            "virtual_environment_id": ve_id, "component_kind": "vem", "component_id": ve_id,
            "bin_start": start, "bin_width_s": 60, "summaries": summaries})

    def _seed_bins(self, name: str, ve_id: str, start: float, now: float) -> None:
        """One bin per minute for the last two days (the charts that read the
        minute tier); older, one per 15 minutes (merged into the 15-minute
        tier the same way, far fewer rows)."""
        top = now - now % MINUTE
        recent = top - 2 * DAY
        t = start - start % (15 * MINUTE)
        while t < recent:
            self._bin(name, ve_id, t, 15 * MINUTE)
            t += 15 * MINUTE
        t = max(t, recent)
        while t < top:
            self._bin(name, ve_id, t, MINUTE)
            t += MINUTE

    # ── what keeps running ───────────────────────────────────────────────────
    def _limits(self, ve_id: str) -> Dict[str, Any]:
        """What the provisioner gives this VE's VEM (plan + quota)."""
        doc = self.db["virtual_environments"].find_one({"_id": ve_id})
        return self.provisioner.vem_limits(V.VirtualEnvironment.from_doc(doc)) if doc else {}

    def heartbeats(self) -> None:
        for doc in self.db["virtual_environments"].find({"lifecycle": {"$ne": V.DELETED}}):
            ve_id = doc["_id"]
            desired = doc.get("desired")
            state = self.vem.state.setdefault(ve_id, {"lifecycle": "Running", "ready_for_removal": False})
            if desired == V.DESIRED_RUNNING and state.get("lifecycle") in ("Stopped", "Sleeping"):
                state["lifecycle"] = "Running"
            lifecycle = state.get("lifecycle", "Running")
            running = self.vem_api.running(ve_id) if lifecycle in ("Running", "Scaling") else 0
            target = self.vem_api.target(ve_id)
            self.intake.handle("VirtualEnvironmentHeartbeat", f"vem:{ve_id}", {
                "virtual_environment_id": ve_id, "lifecycle": lifecycle,
                "health": "Healthy" if running else None, "target_endpoints": target,
                "min_endpoints": int(doc.get("min_endpoints") or 1), "running": running,
                "outbox_pending": 0})

    def tick_minute(self) -> None:
        now = time.time()
        for name, ve_id in self.ve_ids.items():
            doc = self.db["virtual_environments"].find_one({"_id": ve_id}) or {}
            if name == "sandbox" or doc.get("lifecycle") != V.RUNNING:
                continue
            self._bin(name, ve_id, now - now % MINUTE - MINUTE, MINUTE)

    def run_background(self, stop: threading.Event) -> None:
        def beat():
            while not stop.wait(3.0):
                try:
                    self.heartbeats()
                    self.provisioner.tick()
                except Exception as exc:        # keep the dev server alive
                    print("heartbeat:", exc)

        def minutes():
            while not stop.wait(MINUTE):
                try:
                    self.tick_minute()
                    if self.rollups:
                        self.tiers.tick()
                except Exception as exc:
                    print("minute:", exc)

        for target in (beat, minutes):
            threading.Thread(target=target, daemon=True).start()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--port", type=int, default=9000)
    parser.add_argument("--days", type=int, default=30, help="days of sample job records")
    parser.add_argument("--mongo-uri", default="", help="a real MongoDB (its axo_cp_dev database is replaced)")
    args = parser.parse_args()
    started = time.time()
    print("seeding sample data ...", flush=True)
    platform = DevPlatform(seed_days=args.days, mongo_uri=args.mongo_uri)
    print(f"seeded in {time.time() - started:.1f} s: VEs {json.dumps(platform.ve_ids)}", flush=True)
    stop = threading.Event()
    platform.run_background(stop)
    app = create_app(platform.services, cors_origins=["*"],
                     http_client=httpx.AsyncClient(transport=httpx.MockTransport(platform.vem_api)))
    print(f"Control Plane (dev) on http://localhost:{args.port} — sign in as demo / demo-password", flush=True)
    try:
        uvicorn.run(app, host="0.0.0.0", port=args.port, log_level="warning")
    finally:
        stop.set()


if __name__ == "__main__":
    main()
