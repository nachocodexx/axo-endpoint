from __future__ import annotations

import math
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from pymongo.database import Database

from axo_control_plane.application import notifications as N
from axo_control_plane.application.notifications import Notifications
from axo_control_plane.application.ports import HistoryLog, StackDriver, VemGateway
from axo_control_plane.application.provisioner import BACKING_ROLES, Provisioner, labels_for
from axo_control_plane.domain import virtual_environment as V
from axo_control_plane.domain.errors import ConflictError, LimitReachedError, NotFoundError, UnavailableError
from axo_control_plane.domain.virtual_environment import VirtualEnvironment

CP_ACTOR = "control-plane"

OK, FIXED, FAILED, LATER = "ok", "fixed", "failed", "later"

_ROLE_NAMES = {"kurrent": "Event log (Kurrent)", "mongo": "Database (Mongo)", "redis": "Object values (Redis)",
               "vem": "VE manager"}


class VeRepair:
    """Checks every piece a running VE needs and fixes what it can, step by
    step: its network (and axo_storage on it), the Kurrent / Mongo / Redis
    and VEM containers (started, or created when missing), that the VEM
    answers (restarted, then recreated -- its data is kept), and then the
    VEM tries its failed endpoints again at once. Every step is recorded:
    in the answer (the dialog), on the VE (``last_repair``), in the VE's
    history (through the VEM) and as a notification.

    People (Admin+) may repair a VE once every ``every_s``; the CP repairs a
    VE on its own once it has been Unavailable for ``auto_after_s``, at most
    once per ``auto_every_s``, and tells the owner."""

    def __init__(self, db: Database, driver: StackDriver, vem: VemGateway, provisioner: Provisioner,
                 notifications: Notifications, history: HistoryLog, storage_container: str,
                 every_s: float = 300.0, auto_after_s: float = 600.0, auto_every_s: float = 3600.0,
                 vem_wait_s: float = 30.0, poll_s: float = 2.0, now_fn: Callable[[], float] = time.time,
                 sleep_fn: Callable[[float], None] = time.sleep) -> None:
        self._col = db["virtual_environments"]
        self._driver = driver
        self._vem = vem
        self._provisioner = provisioner
        self._notifications = notifications
        self._history = history
        self._storage_container = storage_container
        self.every_s = every_s
        self._auto_after_s = auto_after_s
        self._auto_every_s = auto_every_s
        self._vem_wait_s = vem_wait_s
        self._poll_s = poll_s
        self._now_fn = now_fn
        self._sleep_fn = sleep_fn

    # ── asked by someone ─────────────────────────────────────────────────────
    def repair(self, ve_id: str, by: str, by_name: Optional[str] = None) -> Dict[str, Any]:
        ve = self._running_ve(ve_id)
        now = self._now_fn()
        claimed = self._col.find_one_and_update(
            {"_id": ve_id, "$or": [{"repair_at": None}, {"repair_at": {"$lte": now - self.every_s}}]},
            {"$set": {"repair_at": now}},
        )
        if claimed is None:
            last = (self._col.find_one({"_id": ve_id}, {"repair_at": 1}) or {}).get("repair_at") or now
            left = max(1, int(math.ceil(last + self.every_s - now)))
            raise LimitReachedError(
                f"this VE was repaired a moment ago; it can be repaired again in {math.ceil(left / 60)} min",
                code="REPAIR_TOO_SOON", details={"available_in_s": left, "available_at": last + self.every_s},
            )
        return self._run(ve, by, by_name, automatic=False)

    def status(self, ve_id: str) -> Dict[str, Any]:
        """The last repair and when the next one may run (the ⋮ menu)."""
        doc = self._col.find_one({"_id": ve_id}, {"repair_at": 1, "last_repair": 1}) or {}
        last_at = doc.get("repair_at")
        return {"last_repair": doc.get("last_repair"),
                "available_at": (last_at + self.every_s) if last_at else None, "every_s": self.every_s}

    # ── on its own ───────────────────────────────────────────────────────────
    def check_unavailable(self, ve: VirtualEnvironment) -> Optional[Dict[str, Any]]:
        """Called by the provisioner after each step of a VE that should
        run: tracks how long it has been Unavailable and repairs it once
        that passes ``auto_after_s`` (at most once per ``auto_every_s``)."""
        now = self._now_fn()
        doc = self._col.find_one({"_id": ve.ve_id}, {"unavailable_since": 1, "auto_repair_at": 1}) or {}
        since = doc.get("unavailable_since")
        if ve.health != "Unavailable":
            if since is not None:
                self._col.update_one({"_id": ve.ve_id}, {"$set": {"unavailable_since": None}})
            return None
        if since is None:
            self._col.update_one({"_id": ve.ve_id}, {"$set": {"unavailable_since": now}})
            return None
        if now - since < self._auto_after_s or now - (doc.get("auto_repair_at") or 0.0) < self._auto_every_s:
            return None
        self._col.update_one({"_id": ve.ve_id}, {"$set": {"auto_repair_at": now, "repair_at": now}})
        return self._run(ve, CP_ACTOR, None, automatic=True)

    # ── the steps ────────────────────────────────────────────────────────────
    def _running_ve(self, ve_id: str) -> VirtualEnvironment:
        doc = self._col.find_one({"_id": ve_id})
        if doc is None or doc.get("lifecycle") == V.DELETED:
            raise NotFoundError("no such VE")
        ve = VirtualEnvironment.from_doc(doc)
        if ve.desired != V.DESIRED_RUNNING:
            raise ConflictError("only a VE that should be running can be repaired; start it first",
                                code="REPAIR_NOT_RUNNING")
        return ve

    def _run(self, ve: VirtualEnvironment, by: str, by_name: Optional[str], automatic: bool) -> Dict[str, Any]:
        now = self._now_fn()
        # The provisioner leaves this VE alone while the repair works on it.
        self._col.update_one({"_id": ve.ve_id}, {"$set": {"repair_running_until": now + self._vem_wait_s + 60}})
        steps: List[Dict[str, Any]] = []
        try:
            self._step(steps, "network", "Network", lambda: self._network(ve))
            for role in BACKING_ROLES + ("vem",):
                self._step(steps, role, _ROLE_NAMES[role], lambda role=role: self._container(ve, role))
            answers = self._step(steps, "vem_answers", "VE manager answers", lambda: self._vem_answers(ve, steps))
            if answers == OK or answers == FIXED:
                self._step(steps, "endpoints", "Endpoints", lambda: self._endpoints(ve, by, steps, automatic))
            else:
                steps.append({"check": "endpoints", "name": "Endpoints", "status": LATER,
                              "text": "Failed endpoints are tried again as soon as the VE manager answers."})
                self._col.update_one({"_id": ve.ve_id}, {"$set": {"repair_unreported": {
                    "by": by, "steps": steps, "automatic": automatic}}})
        finally:
            self._col.update_one({"_id": ve.ve_id}, {"$set": {"repair_running_until": None}})
        return self._record(ve, by, by_name, automatic, now, steps)

    def _step(self, steps: List[Dict[str, Any]], check: str, name: str,
              fn: Callable[[], Tuple[str, str]]) -> str:
        try:
            status, text = fn()
        except Exception as exc:   # one broken piece mustn't stop the other checks
            status, text = FAILED, str(exc)[:300] or exc.__class__.__name__
        steps.append({"check": check, "name": name, "status": status, "text": text})
        return status

    def _network(self, ve: VirtualEnvironment) -> Tuple[str, str]:
        created = self._driver.ensure_network(ve.network, labels_for(ve.ve_id, "network"))
        attached = self._driver.connect(ve.network, self._storage_container)
        if created:
            return FIXED, f"{ve.network} was missing: created"
        if attached:
            return FIXED, "The storage service wasn't on the VE's network: attached"
        return OK, f"{ve.network} is there, with the storage service on it"

    def _container(self, ve: VirtualEnvironment, role: str) -> Tuple[str, str]:
        outcome = self._provisioner.ensure_role(ve, role)
        name = ve.container(role)
        if outcome == "running" and self._driver.image_outdated(name, self._provisioner.image_of(ve, role)):
            self._provisioner.recreate(ve, role)
            return FIXED, f"{name} ran an older version of the platform: updated (its data is kept)"
        if outcome == "running":
            return OK, f"{name} is running"
        if outcome == "started":
            return FIXED, f"{name} was stopped: started"
        return FIXED, f"{name} was missing: created (its data is kept)"

    def _answers(self, ve_id: str) -> bool:
        try:
            self._vem.status(ve_id)
            return True
        except UnavailableError:
            return False

    def _wait_for_vem(self, ve_id: str) -> bool:
        deadline = self._now_fn() + self._vem_wait_s
        while True:
            if self._answers(ve_id):
                return True
            if self._now_fn() >= deadline:
                return False
            self._sleep_fn(self._poll_s)

    def _vem_answers(self, ve: VirtualEnvironment, steps: List[Dict[str, Any]]) -> Tuple[str, str]:
        just_started = any(s["check"] == "vem" and s["status"] == FIXED for s in steps)
        if self._answers(ve.ve_id):
            return OK, "The VE manager answers"
        if just_started and self._wait_for_vem(ve.ve_id):
            return FIXED, "The VE manager answers after starting"
        self._driver.stop(ve.container("vem"))
        self._driver.start(ve.container("vem"))
        self._col.update_one({"_id": ve.ve_id}, {"$set": {"vem_started_at": self._now_fn()}})
        if self._wait_for_vem(ve.ve_id):
            return FIXED, "The VE manager wasn't answering: restarted, and it answers now"
        self._provisioner.recreate_vem(ve)
        if self._wait_for_vem(ve.ve_id):
            return FIXED, "The VE manager didn't answer after a restart: recreated (its data is kept), and it answers now"
        return LATER, ("The VE manager didn't answer after a restart: recreated (its data is kept); "
                       "it's still starting up")

    def _endpoints(self, ve: VirtualEnvironment, by: str, steps: List[Dict[str, Any]],
                   automatic: bool) -> Tuple[str, str]:
        try:
            answer = self._vem.repair(ve.ve_id, by, list(steps), automatic) or {}
        except NotFoundError:
            return FAILED, ("The VE manager runs a version without Repair: rebuild the platform's images "
                            "(deploy.sh build), then repair again")
        if answer.get("started"):
            return FIXED, "The VE had failed to start: starting it again"
        retried = list(answer.get("retried") or [])
        counts: Dict[str, int] = answer.get("endpoints") or {}
        target = answer.get("target_endpoints")
        running = counts.get("running", 0)
        sleeping = counts.get("sleeping", 0)
        have = f"{running} of {target} running" if target is not None else f"{running} running"
        if sleeping:
            have += f", {sleeping} sleeping"
        if retried:
            return FIXED, (f"{have}; {len(retried)} failed endpoint{'s' if len(retried) != 1 else ''} "
                           f"tried again now ({', '.join(retried)})")
        if target is not None and running + sleeping < target:
            return LATER, f"{have}; the others are starting"
        return OK, have

    def _record(self, ve: VirtualEnvironment, by: str, by_name: Optional[str], automatic: bool, at: float,
                steps: List[Dict[str, Any]]) -> Dict[str, Any]:
        fixed = [s for s in steps if s["status"] == FIXED]
        failed = [s for s in steps if s["status"] == FAILED]
        outcome = FAILED if failed else (FIXED if fixed else OK)
        report = {"at": at, "by": by, "by_name": by_name, "automatic": automatic, "outcome": outcome, "steps": steps}
        self._col.update_one({"_id": ve.ve_id}, {"$set": {"last_repair": report}})
        self._history.append(f"ve-{ve.ve_id}", "VirtualEnvironmentRepaired", {"ve_id": ve.ve_id, **report})
        who = "The platform" if automatic else (by_name or "Someone")
        if failed:
            text = f"{who} tried to repair VE {ve.name}; {len(failed)} check{'s' if len(failed) != 1 else ''} still failing"
        elif fixed:
            text = f"{who} repaired VE {ve.name}: {len(fixed)} thing{'s' if len(fixed) != 1 else ''} fixed"
        else:
            text = f"{who} checked VE {ve.name}: everything was working"
        if automatic:
            text += f" (it was unavailable for {int(self._auto_after_s // 60)} min)"
        self._notify(ve, by, automatic, text, {"ve_id": ve.ve_id, "repair": outcome})
        return {"ve_id": ve.ve_id, **report, "available_at": at + self.every_s}

    def _notify(self, ve: VirtualEnvironment, by: str, automatic: bool, text: str, data: Dict[str, Any]) -> None:
        owners: List[str] = [ve.owner.id] if ve.owner.kind == V.OWNER_USER else []
        if ve.owner.kind == V.OWNER_TEAM:
            team = self._col.database["teams"].find_one({"_id": ve.owner.id}) or {}
            owners = [team.get("owner_id", "")]
        people = owners + ([] if automatic else [by])
        self._notifications.notify(people, N.VE_STATE, text, data)
