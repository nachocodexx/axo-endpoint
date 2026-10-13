from __future__ import annotations

import threading
import time
import uuid
from typing import Any, Callable, Dict, List, Optional

from pymongo.database import Database

from axo_control_plane.application import notifications as N
from axo_control_plane.application.catalog import PlanCatalog
from axo_control_plane.application.notifications import Notifications
from axo_control_plane.application.ports import HistoryLog, Leases, VemGateway
from axo_control_plane.domain import virtual_environment as V
from axo_control_plane.domain.errors import DomainError, UnavailableError
from axo_control_plane.domain.virtual_environment import VirtualEnvironment

WAITING = "waiting"
DELIVERED = "delivered"
FAILED = "failed"

KIND_JOB = "job"
KIND_CHOREOGRAPHY = "choreography_run"


def new_job_id() -> str:
    return f"job-{uuid.uuid4().hex}"


class SleepManager:
    """The Free plan's sleep, and the requests that arrive meanwhile.

    - A sleeping-plan VE with no work for ``idle_after_s`` goes to sleep: the
      same steps as Stop (containers kept, so waking is faster).
    - A job / choreography run for a sleeping VE is saved here (durable, in
      the shared Mongo), answered at once with its id ("waiting: the VE is
      waking up"), and the VE is woken.
    - Once the VE runs again, the saved requests are delivered in order, with
      the same id the person already got (the VEM runs an id once).
    - Waking counts like a cold start: max_wait=0 still runs; max_wait=N
      fails (not billed) if the wake took longer than N.
    - If the VE can't wake within ``wake_timeout_s``, the saved requests fail
      as a platform problem (not billed) and the owner is told.
    """

    def __init__(self, db: Database, catalog: PlanCatalog, vem: VemGateway, leases: Leases,
                 notifications: Notifications, history: HistoryLog, instance_id: str,
                 idle_after_s: float = 1800.0, wake_timeout_s: float = 300.0,
                 now_fn: Callable[[], float] = time.time) -> None:
        self._ves = db["virtual_environments"]
        self._pending = db["pending_requests"]
        self._catalog = catalog
        self._vem = vem
        self._leases = leases
        self._notifications = notifications
        self._history = history
        self._instance_id = instance_id
        self._idle_after_s = idle_after_s
        self._wake_timeout_s = wake_timeout_s
        self._now_fn = now_fn

    # ── loop ─────────────────────────────────────────────────────────────────
    def run_forever(self, stop_event: threading.Event, interval_s: float = 5.0) -> None:
        while not stop_event.wait(interval_s):
            try:
                self.tick()
            except Exception:
                pass

    def tick(self) -> None:
        self.put_idle_to_sleep()
        self.deliver()

    # ── going to sleep ───────────────────────────────────────────────────────
    def put_idle_to_sleep(self) -> List[str]:
        now = self._now_fn()
        sleeping_plans = [p.plan_id for p in self._catalog.plans(include_inactive=True) if p.sleeps]
        slept: List[str] = []
        for doc in self._ves.find({"plan_id": {"$in": sleeping_plans}, "desired": V.DESIRED_RUNNING,
                                   "lifecycle": V.RUNNING, "idle_since": {"$ne": None}}):
            if now - doc["idle_since"] < self._idle_after_s:
                continue
            if self._pending.count_documents({"ve_id": doc["_id"], "status": WAITING}):
                continue
            result = self._ves.update_one(
                {"_id": doc["_id"], "desired": V.DESIRED_RUNNING, "lifecycle": V.RUNNING},
                {"$set": {"desired": V.DESIRED_SLEEPING, "lifecycle": V.STOPPING,
                          "lifecycle_reason": f"idle for {int(self._idle_after_s // 60)} minutes"},
                 "$inc": {"version": 1}})
            if result.modified_count:
                slept.append(doc["_id"])
                self._history.append(f"ve-{doc['_id']}", "VirtualEnvironmentSleeping",
                                     {"ve_id": doc["_id"], "idle_since": doc["idle_since"], "at": now})
        return slept

    # ── requests while asleep ────────────────────────────────────────────────
    def is_asleep(self, ve: VirtualEnvironment) -> bool:
        return ve.desired == V.DESIRED_SLEEPING or ve.lifecycle in (V.SLEEPING, V.WAKING) or \
            (ve.desired == V.DESIRED_RUNNING and self._pending.count_documents({"ve_id": ve.ve_id, "status": WAITING}) > 0)

    def accept(self, ve_id: str, kind: str, path: str, body: Dict[str, Any], acting_user: str,
               max_wait: Optional[float] = None) -> Dict[str, Any]:
        """Save one request for a sleeping VE and wake it. ``path`` is the VEM
        route it will be delivered to."""
        now = self._now_fn()
        request_id = new_job_id() if kind == KIND_JOB else f"run-{uuid.uuid4().hex}"
        id_field = "job_id" if kind == KIND_JOB else "run_id"
        self._pending.insert_one({
            "_id": request_id, "ve_id": ve_id, "kind": kind, "path": path,
            "body": {**body, id_field: request_id}, "acting_user": acting_user, "max_wait": max_wait,
            "status": WAITING, "created_at": now, "attempts": 0,
        })
        self.wake(ve_id, reason=f"request from {acting_user}")
        return {id_field: request_id, "status": WAITING, "reason": "the VE is waking up"}

    def wake(self, ve_id: str, reason: str) -> bool:
        now = self._now_fn()
        result = self._ves.update_one(
            {"_id": ve_id, "desired": V.DESIRED_SLEEPING},
            {"$set": {"desired": V.DESIRED_RUNNING, "lifecycle": V.WAKING, "lifecycle_reason": reason,
                      "wake_requested_at": now, "step": "", "step_error": ""},
             "$inc": {"version": 1}})
        if result.modified_count:
            self._history.append(f"ve-{ve_id}", "VirtualEnvironmentWaking", {"ve_id": ve_id, "reason": reason,
                                                                             "at": now})
        return bool(result.modified_count)

    def status(self, request_id: str) -> Optional[Dict[str, Any]]:
        doc = self._pending.find_one({"_id": request_id})
        if doc is None or doc["status"] == DELIVERED:
            return None
        return {"job_id" if doc["kind"] == KIND_JOB else "run_id": doc["_id"], "status": doc["status"],
                "reason": doc.get("error") or "the VE is waking up", "ve_id": doc["ve_id"]}

    # ── delivering after the wake ────────────────────────────────────────────
    def deliver(self) -> int:
        delivered = 0
        for ve_id in self._pending.distinct("ve_id", {"status": WAITING}):
            if not self._leases.acquire(f"deliver:{ve_id}", self._instance_id, 30.0):
                continue
            doc = self._ves.find_one({"_id": ve_id})
            if doc is None or doc.get("desired") == V.DESIRED_DELETED:
                self._fail_all(ve_id, "the VE was deleted")
                continue
            if doc.get("lifecycle") in (V.RUNNING, V.SCALING):
                delivered += self._deliver_ve(ve_id, doc)
            elif doc.get("lifecycle") == V.WAKING and doc.get("wake_requested_at") and \
                    self._now_fn() - doc["wake_requested_at"] > self._wake_timeout_s:
                self._fail_all(ve_id, f"the VE did not wake up within {int(self._wake_timeout_s)} s")
                self._notify_owner(doc, f"VE {doc.get('name')} could not wake up; waiting requests failed")
        return delivered

    def _deliver_ve(self, ve_id: str, ve_doc: Dict[str, Any]) -> int:
        delivered = 0
        woke_at = ve_doc.get("wake_requested_at")
        for req in self._pending.find({"ve_id": ve_id, "status": WAITING}).sort("created_at", 1):
            waited = self._now_fn() - req["created_at"]
            if req.get("max_wait") not in (None, -1, 0) and waited > float(req["max_wait"]):
                self._mark(req["_id"], FAILED, error=f"timed out waiting for the VE to wake ({waited:.0f} s)",
                           outcome="timed_out_in_queue")
                continue
            try:
                self._vem.submit_job(ve_id, {"path": req["path"], "body": req["body"],
                                             "acting_user": req["acting_user"]})
            except UnavailableError:
                self._pending.update_one({"_id": req["_id"]}, {"$inc": {"attempts": 1}})
                break                                  # try again next tick, keep order
            except DomainError as exc:
                self._mark(req["_id"], FAILED, error=exc.message, outcome="failed_platform")
                continue
            self._mark(req["_id"], DELIVERED, wake_ms=round((self._now_fn() - (woke_at or req["created_at"])) * 1000))
            delivered += 1
        return delivered

    def _mark(self, request_id: str, status: str, **fields: Any) -> None:
        self._pending.update_one({"_id": request_id}, {"$set": {"status": status, "done_at": self._now_fn(),
                                                                **fields}})

    def _fail_all(self, ve_id: str, error: str) -> None:
        self._pending.update_many({"ve_id": ve_id, "status": WAITING},
                                  {"$set": {"status": FAILED, "error": error, "outcome": "failed_platform",
                                            "done_at": self._now_fn()}})

    def _notify_owner(self, doc: Dict[str, Any], text: str) -> None:
        owner = doc.get("owner") or {}
        user_id = owner.get("id") if owner.get("kind") == V.OWNER_USER else \
            (self._ves.database["teams"].find_one({"_id": owner.get("id")}) or {}).get("owner_id")
        self._notifications.notify([user_id], N.VE_STATE, text, {"ve_id": doc["_id"]})
