from __future__ import annotations

import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from axo_shared.error_catalog import code_of
from pymongo import ReturnDocument
from pymongo.database import Database

from axo_control_plane.application import notifications as N
from axo_control_plane.application.authz import Authorizer
from axo_control_plane.application.catalog import PlanCatalog
from axo_control_plane.application.notifications import Notifications
from axo_control_plane.application.people import Actor
from axo_control_plane.application.sleeping import KIND_CHOREOGRAPHY, KIND_EVENT, KIND_JOB, SleepManager
from axo_control_plane.domain import roles as R
from axo_control_plane.domain import virtual_environment as V
from axo_control_plane.domain.errors import ConflictError, LimitReachedError, NotFoundError, UnavailableError
from axo_control_plane.domain.virtual_environment import VirtualEnvironment

# ── which VEM calls go through, and the lowest role for each ────────────────
# (method, path pattern, lowest role, submission kind). Anything not listed is
# refused: adding a VEM route means adding one line here.
_ID = r"[^/]+"
RULES: List[Tuple[str, "re.Pattern[str]", str, Optional[str]]] = [
    (m, re.compile(f"^{p}$"), role, kind) for m, p, role, kind in [
        # reading: every role
        ("GET", r"/ve", R.VIEWER, None),
        ("GET", r"/ve/settings", R.VIEWER, None),
        ("GET", r"/endpoints", R.VIEWER, None),
        ("GET", rf"/endpoints/{_ID}", R.VIEWER, None),
        ("GET", rf"/endpoints/{_ID}/history", R.VIEWER, None),
        ("GET", rf"/endpoints/{_ID}/jobs/{_ID}", R.VIEWER, None),
        ("GET", r"/functions", R.VIEWER, None),
        ("GET", rf"/functions/{_ID}", R.VIEWER, None),
        ("GET", rf"/functions/{_ID}/history", R.VIEWER, None),
        ("GET", rf"/functions/{_ID}/jobs", R.VIEWER, None),
        ("GET", r"/jobs", R.VIEWER, None),
        ("GET", rf"/jobs/{_ID}", R.VIEWER, None),
        ("GET", rf"/jobs/{_ID}/result", R.VIEWER, None),
        ("GET", r"/ve/results/shortened", R.VIEWER, None),
        ("GET", rf"/functions/{_ID}/{_ID}/code", R.VIEWER, None),     # everyone with access reads the code
        ("GET", r"/history", R.VIEWER, None),
        ("GET", rf"/active-objects/{_ID}/history", R.VIEWER, None),
        ("GET", rf"/events/{_ID}/{_ID}", R.VIEWER, None),
        ("GET", r"/consensus/leader", R.VIEWER, None),
        ("GET", r"/object-classes", R.VIEWER, None),
        ("GET", rf"/object-classes/{_ID}", R.VIEWER, None),
        ("GET", r"/objects", R.VIEWER, None),
        ("GET", rf"/objects/{_ID}", R.VIEWER, None),
        ("GET", rf"/objects/{_ID}/history", R.VIEWER, None),
        ("GET", rf"/objects/{_ID}/state", R.VIEWER, None),
        ("GET", r"/choreographies", R.VIEWER, None),
        ("GET", r"/choreographies/designer", R.VIEWER, None),
        ("GET", rf"/choreographies/{_ID}", R.VIEWER, None),
        ("GET", rf"/choreographies/{_ID}/versions", R.VIEWER, None),
        ("GET", rf"/choreographies/{_ID}/usage", R.VIEWER, None),
        ("GET", rf"/choreographies/{_ID}/history", R.VIEWER, None),
        ("GET", rf"/choreographies/{_ID}/runs", R.VIEWER, None),
        ("GET", rf"/choreographies/{_ID}/runs/{_ID}", R.VIEWER, None),
        ("GET", rf"/choreographies/{_ID}/runs/{_ID}/result", R.VIEWER, None),
        ("GET", rf"/choreography-runs/{_ID}", R.VIEWER, None),
        ("GET", rf"/visibility/{_ID}/{_ID}", R.VIEWER, None),
        ("GET", r"/events", R.VIEWER, None),
        ("GET", rf"/events/{_ID}", R.VIEWER, None),
        ("GET", r"/event-types", R.VIEWER, None),
        ("GET", r"/results", R.VIEWER, None),             # the results bucket, filtered per person
        ("GET", r"/choreography-suggestions", R.VIEWER, None),
        ("GET", r"/policies", R.VIEWER, None),
        ("GET", r"/policies/templates", R.VIEWER, None),
        ("GET", r"/policies/decisions", R.VIEWER, None),
        # running and building: Developer
        ("POST", rf"/functions/{_ID}/{_ID}/jobs", R.DEVELOPER, KIND_JOB),
        ("POST", rf"/endpoints/{_ID}/jobs", R.DEVELOPER, KIND_JOB),
        ("POST", rf"/jobs/{_ID}/cancel", R.DEVELOPER, None),
        ("POST", rf"/jobs/{_ID}/retry", R.DEVELOPER, None),
        ("POST", r"/functions/check", R.DEVELOPER, None),               # the browser's live check (never runs code)
        ("POST", r"/functions", R.DEVELOPER, None),
        ("PATCH", rf"/functions/{_ID}/{_ID}", R.DEVELOPER, None),
        ("PUT", rf"/functions/{_ID}/default", R.DEVELOPER, None),      # the version that runs when none is named
        ("DELETE", rf"/functions/{_ID}/{_ID}", R.DEVELOPER, None),
        ("DELETE", rf"/functions/{_ID}/{_ID}/purge", R.DEVELOPER, None),
        ("POST", r"/object-classes", R.DEVELOPER, None),
        ("POST", r"/object-classes/check", R.DEVELOPER, None),         # the browser's live check; never runs it
        ("DELETE", rf"/object-classes/{_ID}/{_ID}", R.DEVELOPER, None),  # the VEM: own versions, or Admin
        ("POST", r"/objects", R.DEVELOPER, KIND_JOB),                    # runs __init__ as a job
        ("POST", rf"/objects/{_ID}/calls", R.DEVELOPER, KIND_JOB),
        ("POST", rf"/objects/{_ID}/upgrade", R.DEVELOPER, None),
        ("POST", rf"/objects/{_ID}/clone", R.DEVELOPER, None),
        ("POST", rf"/objects/{_ID}/restore", R.DEVELOPER, None),
        ("PATCH", rf"/objects/{_ID}", R.DEVELOPER, None),                # its own settings (revisions kept)
        ("POST", r"/choreographies", R.DEVELOPER, None),
        ("POST", r"/choreographies/save", R.DEVELOPER, None),
        ("POST", r"/choreographies/check", R.DEVELOPER, None),
        ("PUT", rf"/choreographies/{_ID}", R.DEVELOPER, None),
        ("POST", rf"/choreographies/{_ID}/runs", R.DEVELOPER, KIND_CHOREOGRAPHY),
        ("POST", rf"/choreographies/{_ID}/runs/{_ID}/cancel", R.DEVELOPER, None),
        ("POST", rf"/choreographies/{_ID}/runs/{_ID}/resume", R.DEVELOPER, None),
        ("POST", rf"/choreographies/{_ID}/triggers", R.DEVELOPER, None),
        ("DELETE", rf"/choreographies/{_ID}/triggers/{_ID}", R.DEVELOPER, None),
        # who sees runs and results: its creator or the VE's Admins (the VEM checks which)
        ("PUT", rf"/visibility/{_ID}/{_ID}", R.DEVELOPER, None),
        # events: sending one starts runs (kept while the VE sleeps, like a run)
        ("POST", r"/events", R.DEVELOPER, KIND_EVENT),
        ("PUT", rf"/event-types/{_ID}", R.DEVELOPER, None),
        # running the VE, and deleting objects / choreographies: Admin
        ("PATCH", r"/ve/settings", R.ADMIN, None),
        ("POST", r"/endpoints", R.ADMIN, None),
        ("DELETE", rf"/endpoints/{_ID}", R.ADMIN, None),
        ("POST", rf"/endpoints/{_ID}/restart", R.ADMIN, None),
        ("DELETE", rf"/endpoints/{_ID}/purge", R.ADMIN, None),
        ("DELETE", rf"/objects/{_ID}", R.ADMIN, None),
        ("DELETE", rf"/choreographies/{_ID}", R.ADMIN, None),
        ("DELETE", rf"/choreographies/{_ID}/purge", R.ADMIN, None),
        ("DELETE", rf"/event-types/{_ID}", R.ADMIN, None),
        ("POST", r"/policies", R.ADMIN, None),
        ("PATCH", rf"/policies/{_ID}", R.ADMIN, None),
        ("DELETE", rf"/policies/{_ID}", R.ADMIN, None),
        ("POST", r"/policies/pause", R.ADMIN, None),
    ]
]
# Live-update streams (WebSocket) -- reading, so Viewer.
WS_RULES = [re.compile(p) for p in (r"^/ws/endpoints$", rf"^/ws/endpoints/{_ID}$", r"^/ws/functions$",
                                     r"^/ws/objects$", rf"^/ws/objects/{_ID}$",
                                     r"^/ws/jobs$", rf"^/ws/jobs/{_ID}$",
                                     r"^/ws/choreographies$", rf"^/ws/choreographies/{_ID}$")]
# VEM routes only the Control Plane itself calls (never passed through).
CONTROL_PLANE_ONLY = [("PUT", "/ve/limits"), ("POST", "/ve/prepare-delete"), ("POST", "/ve/start"),
                      ("POST", "/ve/stop"), ("POST", "/ve/repair"), ("POST", "/bucket-events"), ("GET", "/trigger-buckets")]


# Calls that can change which buckets the VE's triggers watch: once one
# succeeds the front door reads the VEM's trigger list before answering
# (bucket_events.save_triggers), so a file written right after is routed.
TRIGGER_CHANGES = [(m, re.compile(p)) for m, p in (
    ("POST", r"^/choreographies$"), ("POST", r"^/choreographies/save$"), ("PUT", rf"^/choreographies/{_ID}$"),
    ("POST", rf"^/choreographies/{_ID}/triggers$"), ("DELETE", rf"^/choreographies/{_ID}/triggers/{_ID}$"),
    ("DELETE", rf"^/choreographies/{_ID}$"), ("DELETE", rf"^/choreographies/{_ID}/purge$"),
)]


def changes_triggers(method: str, path: str) -> bool:
    return any(m == method.upper() and p.match(path) for m, p in TRIGGER_CHANGES)


def match(method: str, path: str) -> Optional[Tuple[str, Optional[str]]]:
    """(lowest role, submission kind) for a VEM call, or None if not allowed."""
    for rule_method, pattern, role, kind in RULES:
        if rule_method == method.upper() and pattern.match(path):
            return role, kind
    return None


@dataclass(frozen=True)
class Decision:
    """What to do with one VEM call."""

    action: str                          # "forward" | "answer"
    ve: Optional[VirtualEnvironment] = None
    status_code: int = 200
    body: Optional[Dict[str, Any]] = None
    headers: Optional[Dict[str, str]] = None
    role: Optional[str] = None           # the caller's role in the VE (sent to the VEM)
    teams: Tuple[str, ...] = ()          # the caller's team ids (who sees runs shared with teams)


# Who is calling, for the VEM: their role (what they may see, e.g. a job's
# traceback), their name, and the SDK key they used (if any).
ACTING_ROLE_HEADER = "X-Acting-Role"
ACTING_NAME_HEADER = "X-Acting-Name"
ACTING_KEY_HEADER = "X-Acting-Key"
ACTING_KEY_NAME_HEADER = "X-Acting-Key-Name"
ACTING_TEAMS_HEADER = "X-Acting-Teams"


def acting_headers(actor: Actor, role: Optional[str], teams: Sequence[str] = ()) -> Dict[str, str]:
    headers = {ACTING_NAME_HEADER: actor.username}
    if teams:
        headers[ACTING_TEAMS_HEADER] = ",".join(teams)
    if role:
        headers[ACTING_ROLE_HEADER] = role
    if actor.key_id:
        headers[ACTING_KEY_HEADER] = actor.key_id
        if actor.extra.get("key_name"):
            headers[ACTING_KEY_NAME_HEADER] = str(actor.extra["key_name"])
    return headers


class FrontDoor:
    """Every UI / SDK call into a VE passes here before its VEM:

    1. the caller's role in the VE is enough for this call (table above;
       unknown calls are refused);
    2. the VE can take it: a sleeping VE keeps job / choreography submissions
       (answered at once with an id) and wakes; other calls get "waking up,
       try again"; a stopped / deleting VE refuses;
    3. the Free plan's monthly request limit (submissions only; one
       choreography run = one request), with notices at 80 / 90 / 100 %.
    Then the call goes to the VEM as is, acting for the caller.
    """

    def __init__(self, db: Database, authz: Authorizer, sleep: SleepManager, catalog: PlanCatalog,
                 notifications: Notifications, now_fn: Callable[[], float] = time.time) -> None:
        self._db = db
        self._ves = db["virtual_environments"]
        self._counts = db["request_counts"]
        self._authz = authz
        self._sleep = sleep
        self._catalog = catalog
        self._notifications = notifications
        self._now_fn = now_fn

    def _record(self, ve_id: str) -> VirtualEnvironment:
        doc = self._ves.find_one({"_id": ve_id})
        if doc is None or doc.get("desired") == V.DESIRED_DELETED:
            raise NotFoundError("no such VE")
        return VirtualEnvironment.from_doc(doc)

    def decide(self, actor: Actor, ve_id: str, method: str, path: str,
               body: Optional[Dict[str, Any]] = None) -> Decision:
        rule = match(method, path)
        if rule is None:
            raise NotFoundError(f"{method.upper()} {path} is not available through the Control Plane")
        role, kind = rule
        caller_role = self._authz.require_ve(actor, ve_id, role)
        ve = self._record(ve_id)
        if method.upper() == "GET" and path.startswith("/jobs/"):
            waiting = self._sleep.status(path.split("/", 2)[2])
            if waiting is not None:
                return Decision("answer", ve, 200, waiting)
        cancel = re.fullmatch(rf"/jobs/({_ID})/cancel", path) if method.upper() == "POST" else None
        if cancel is not None:
            dropped = self._sleep.cancel(cancel.group(1), actor.user_id)
            if dropped is not None:                    # saved while the VE slept: never delivered
                return Decision("answer", ve, 200, dropped)
        if ve.desired == V.DESIRED_STOPPED:
            raise ConflictError("the VE is stopped; start it first", code="VE_STOPPED")
        if kind is not None:
            self._count_request(ve)
        if self._sleep.is_asleep(ve):
            if kind is not None:
                accepted = self._sleep.accept(ve_id, kind, path, dict(body or {}), actor.user_id,
                                              max_wait=(body or {}).get("max_wait"))
                return Decision("answer", ve, 202, accepted)
            self._sleep.wake(ve_id, reason=f"request from {actor.username}")
            return Decision("answer", ve, 503, {"error": "VE_WAKING", "error_code": code_of("VE_WAKING"),
                                       "message": "the VE is waking up; try again"},
                            {"Retry-After": "5"})
        if ve.lifecycle in (V.CREATING, V.STARTING, V.RESTORING, V.WAKING) or not ve.heartbeat_at:
            return Decision("answer", ve, 503, {"error": "VE_STARTING", "error_code": code_of("VE_STARTING"),
                                       "message": f"the VE is {ve.lifecycle.lower()}"},
                            {"Retry-After": "5"})
        if ve.health == "Unavailable" and ve.vem_down_since is not None:
            raise UnavailableError("the VE manager is not responding right now", code="VEM_DOWN")
        return Decision("forward", ve, role=caller_role, teams=self._teams_of(actor.user_id))

    def _teams_of(self, user_id: str) -> Tuple[str, ...]:
        """The caller's teams, from the CP's own team records (no Xolo call)."""
        docs = self._db["teams"].find({f"members.{user_id}": {"$exists": True}, "deleted": {"$ne": True}}, {"_id": 1})
        return tuple(sorted(str(d["_id"]) for d in docs))

    def check_ws(self, actor: Actor, ve_id: str, path: str) -> VirtualEnvironment:
        if not any(p.match(path) for p in WS_RULES):
            raise NotFoundError(f"{path} is not available through the Control Plane")
        self._authz.require_ve(actor, ve_id, R.VIEWER)
        return self._record(ve_id)

    # ── Free plan request limit (per rental period, upgrade.md 13.2) ─────────
    def _count_request(self, ve: VirtualEnvironment) -> None:
        plan = self._catalog.get(ve.plan_id)
        if not plan.request_limit:
            return
        key = f"{ve.ve_id}:p{ve.period_index}"
        current = (self._counts.find_one({"_id": key}) or {}).get("count", 0)
        if current >= plan.request_limit:
            raise LimitReachedError(
                f"this VE used all {plan.request_limit} requests of the {plan.name} plan for this period",
                code="REQUEST_LIMIT", details={"limit": plan.request_limit, "period_end": ve.period_end})
        doc = self._counts.find_one_and_update({"_id": key}, {"$inc": {"count": 1}}, upsert=True,
                                               return_document=ReturnDocument.AFTER)
        used = doc["count"] / plan.request_limit
        for threshold in (0.8, 0.9, 1.0):
            if used >= threshold:
                self._notifications.notify(
                    N.bill_viewers(self._db, ve.owner.to_dict()), N.LIMIT_WARNING,
                    f"VE {ve.name} used {int(threshold * 100)}% of this period's {plan.request_limit} requests",
                    {"ve_id": ve.ve_id, "used": doc["count"], "limit": plan.request_limit,
                     "period_end": ve.period_end},
                    dedup_key=f"requests:{key}:{int(threshold * 100)}")
