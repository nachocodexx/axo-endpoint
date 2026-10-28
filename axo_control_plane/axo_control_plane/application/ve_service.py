from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Tuple

from pymongo.database import Database
from pymongo.errors import DuplicateKeyError

from axo_control_plane.application import notifications as N
from axo_control_plane.application.access import AccessResolver
from axo_control_plane.application.authz import Authorizer
from axo_control_plane.application.capacity import CapacityLedger
from axo_control_plane.application.catalog import PlanCatalog
from axo_control_plane.application.notifications import Notifications
from axo_control_plane.application.people import Actor
from axo_control_plane.application.ports import HistoryLog, IdentityProvider
from axo_control_plane.application.teams import Teams
from axo_control_plane.domain import roles as R
from axo_control_plane.domain import settings as S
from axo_control_plane.domain import virtual_environment as V
from axo_control_plane.domain.errors import ConflictError, ForbiddenError, InvalidError, NotFoundError
from axo_control_plane.domain.names import check_ve_name, new_ve_id
from axo_control_plane.domain.periods import BILLING_CLOCK, add_months, rental_period
from axo_control_plane.domain.plans import Plan, Resources, VeShape, check_shape, endpoint_size
from axo_control_plane.domain.virtual_environment import Owner, VirtualEnvironment


@dataclass(frozen=True)
class PurchaseRequest:
    name: str
    plan_id: str
    quota_cpu_cores: float
    quota_memory_mb: int
    endpoint_size: str = "small"
    endpoint_cpu_cores: Optional[float] = None
    endpoint_memory_mb: Optional[int] = None
    target_endpoints: int = 1
    min_endpoints: int = 1
    team_id: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.__dict__)


def month_of(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=BILLING_CLOCK).strftime("%Y-%m")


def month_bounds(ts: float) -> Tuple[float, float]:
    """[start, end) of the calendar month (UTC) holding ``ts``."""
    first = datetime.fromtimestamp(ts, tz=BILLING_CLOCK).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return first.timestamp(), add_months(first.timestamp(), 1)


class VeService:
    """Buying VEs and the VE-wide operations people ask for. The actual
    container work is the provisioner's (desired state): this only records
    what the VE should be, after the checks.

    Checks when buying:
    - the target fits the plan, the quota fits target x size;
    - quota + plan base fit the free platform capacity (reserved at once);
    - Free: personal only, one per person;
    - buying for a team needs Team Owner / Admin; a Team Admin's purchase
      over a spending limit becomes a pending request for the Team Owner.

    Rental (upgrade.md 13.2): a VE is rented in monthly periods from its
    purchase moment, the full plan fee per period (``ve_charges``, one line
    per fee, fixed ids so nothing is charged twice). An upgrade applies at
    once and pays the fee difference; a downgrade waits for the next
    renewal; cancelling stops the renewals, the VE stops when the period
    ends and is deleted after the grace period.
    """

    def __init__(self, db: Database, catalog: PlanCatalog, capacity: CapacityLedger, identity: IdentityProvider,
                 access: AccessResolver, authz: Authorizer, teams: Teams, notifications: Notifications,
                 history: HistoryLog, max_custom: Optional[Resources] = None, grace_s: float = 30 * 86400,
                 now_fn: Callable[[], float] = time.time) -> None:
        self._db = db
        self._col = db["virtual_environments"]
        self._purchases = db["purchases"]
        self._charges = db["ve_charges"]
        self._grace_s = grace_s
        self._catalog = catalog
        self._capacity = capacity
        self._identity = identity
        self._access = access
        self._authz = authz
        self.repairs: Any = None              # VeRepair, set by the server
        self._teams = teams
        self._notifications = notifications
        self._history = history
        self._max_custom = max_custom
        self._now_fn = now_fn

    # ── reading ──────────────────────────────────────────────────────────────
    def record(self, ve_id: str) -> VirtualEnvironment:
        doc = self._col.find_one({"_id": ve_id})
        if doc is None:
            raise NotFoundError("no such VE")
        return VirtualEnvironment.from_doc(doc)

    def get(self, actor: Actor, ve_id: str) -> Dict[str, Any]:
        ve = self.record(ve_id)
        if not ve.visible:
            raise NotFoundError("no such VE")
        role = None
        if not actor.is_platform_admin:
            role = self._authz.require_ve(actor, ve_id, R.VIEWER)
        else:
            role = self._authz.ve_role(actor, ve_id)
        return {**ve.public(), "my_role": role, **self._requests(ve)}

    def _requests(self, ve: VirtualEnvironment) -> Dict[str, Any]:
        """A plan with a request limit (Free): how many this period has used
        (the run form shows it by the button)."""
        try:
            plan = self._catalog.get(ve.plan_id)
        except NotFoundError:
            return {}
        if not plan.request_limit:
            return {}
        doc = self._db["request_counts"].find_one({"_id": f"{ve.ve_id}:p{ve.period_index}"}) or {}
        return {"requests": {"used": int(doc.get("count", 0)), "limit": plan.request_limit}}

    def list_for(self, actor: Actor) -> List[Dict[str, Any]]:
        """VEs the person can see; the Platform Admin sees all (operational)."""
        if actor.is_platform_admin and actor.key_id is None:
            docs = self._col.find({"desired": {"$ne": V.DESIRED_DELETED}})
        else:
            ids = [r.split(":", 1)[1] for r in self._access.visible_resources(actor.user_id) if r.startswith("ve:")]
            if actor.key_ve_id:
                ids = [i for i in ids if i == actor.key_ve_id]
            docs = self._col.find({"_id": {"$in": ids}, "desired": {"$ne": V.DESIRED_DELETED}})
        out = []
        for doc in docs.sort("created_at", 1):
            ve = VirtualEnvironment.from_doc(doc)
            out.append({**ve.public(), "my_role": self._authz.ve_role(actor, ve.ve_id), **self._requests(ve)})
        return out

    # ── buying ───────────────────────────────────────────────────────────────
    def buy(self, actor: Actor, request: PurchaseRequest) -> Dict[str, Any]:
        if actor.is_guest or actor.key_id is not None:
            raise ForbiddenError("only a signed-in user (not a guest or an API key) can buy a VE")
        plan = self._catalog.get(request.plan_id)
        if not plan.active:
            raise InvalidError(f"the {plan.name} plan is not offered any more")
        shape = self._shape(plan, request)
        check_ve_name(request.name)
        owner = self._owner_for(actor, plan, request)
        team_role = None
        if request.team_id:
            team_role = self._teams.require(actor, request.team_id, R.ADMIN)
            if team_role != R.OWNER and plan.monthly_fee_cents > 0:
                over = self._over_limit(actor, request.team_id, plan)
                if over:
                    return self._make_pending(actor, request, plan, over)
        ve = self._create(actor, owner, plan, shape, request.name)
        if request.team_id:
            self._record_purchase(actor, request, plan, "approved", ve.ve_id)
            if team_role != R.OWNER:
                team = self._teams.get(request.team_id)
                self._notifications.notify([team["owner_id"]], N.TEAM_PURCHASE,
                                           f"{actor.username} bought VE {ve.name} ({plan.name}) for team {team['name']}",
                                           {"ve_id": ve.ve_id, "team_id": request.team_id})
        return {"status": "created", "ve": ve.public()}

    def _shape(self, plan: Plan, request: PurchaseRequest) -> VeShape:
        size_name = plan.fixed_endpoint_size or request.endpoint_size
        size = endpoint_size(size_name, request.endpoint_cpu_cores, request.endpoint_memory_mb, self._max_custom)
        quota = Resources(float(request.quota_cpu_cores), int(request.quota_memory_mb))
        if plan.fixed_endpoint_size:
            quota = size.times(plan.max_endpoints)       # Free: fixed, 1 small endpoint
        shape = VeShape(quota=quota, endpoint_size=size, endpoint_size_name=size_name,
                        target_endpoints=int(request.target_endpoints), min_endpoints=int(request.min_endpoints))
        check_shape(plan, shape)
        return shape

    def _owner_for(self, actor: Actor, plan: Plan, request: PurchaseRequest) -> Owner:
        if request.team_id:
            if plan.personal_only:
                raise InvalidError(f"the {plan.name} plan is personal only")
            team = self._teams.get(request.team_id)
            return Owner(V.OWNER_TEAM, team["team_id"], team["name"])
        if plan.one_per_user and self._col.count_documents(
                {"owner.kind": V.OWNER_USER, "owner.id": actor.user_id, "plan_id": plan.plan_id,
                 "desired": {"$ne": V.DESIRED_DELETED}}):
            raise ConflictError(f"you already have a {plan.name} VE (one per person)", code="ONE_FREE_VE")
        return Owner(V.OWNER_USER, actor.user_id, actor.username)

    def _create(self, actor: Actor, owner: Owner, plan: Plan, shape: VeShape, name: str) -> VirtualEnvironment:
        name = check_ve_name(name)
        if self._col.count_documents({"owner.id": owner.id, "name": name, "desired": {"$ne": V.DESIRED_DELETED}}):
            raise ConflictError(f"{owner.name} already has a VE named {name!r}", code="VE_NAME_TAKEN")
        reserved = shape.quota + plan.base
        self._capacity.reserve(reserved)
        now = self._now_fn()
        start, end = rental_period(now, 0, 0)
        ve = VirtualEnvironment(
            ve_id=new_ve_id(name), name=name, owner=owner, plan_id=plan.plan_id, quota=shape.quota,
            endpoint_size_name=shape.endpoint_size_name, endpoint_size=shape.endpoint_size,
            target_endpoints=shape.target_endpoints, min_endpoints=shape.min_endpoints, reserved=reserved,
            created_by=actor.user_id, created_at=now, desired=V.DESIRED_RUNNING, lifecycle=V.CREATING,
            period_anchor=now, period_start=start, period_end=end, period_fee_cents=plan.monthly_fee_cents,
        )
        try:
            self._col.insert_one(ve.to_doc())
            self._give_ownership(ve)
        except Exception:
            self._capacity.release(reserved)
            self._col.delete_one({"_id": ve.ve_id})
            raise
        self._charge(ve, 0, "plan_fee", plan, plan.monthly_fee_cents, actor.user_id, f"{ve.ve_id}:0:fee")
        self._history.append(f"ve-{ve.ve_id}", "VirtualEnvironmentBought",
                             {"ve": ve.public(), "plan_id": plan.plan_id, "fee_cents": plan.monthly_fee_cents,
                              "reserved": reserved.to_dict(), "by": actor.user_id, "at": now})
        return ve

    def _give_ownership(self, ve: VirtualEnvironment) -> None:
        resource = R.ve_resource(ve.ve_id)
        if ve.owner.kind == V.OWNER_USER:
            self._identity.claim(resource, ve.owner.id, "USER")
            self._access.changed_principal(ve.owner.id)
        else:
            groups = self._teams.groups(ve.owner.id)
            self._identity.claim(resource, groups["owner"], "GROUP")
            self._identity.grant(resource, groups["admins"], "GROUP", R.XOLO_PERMISSIONS[R.ADMIN])
            self._identity.grant(resource, groups["members"], "GROUP", R.XOLO_PERMISSIONS[R.VIEWER])
        self._access.changed_resource(resource)

    # ── team spending limits (calendar months) ───────────────────────────────
    def _month_fees(self, team_id: str) -> List[Dict[str, Any]]:
        """Every fee line of the team's VEs this calendar month: purchases,
        renewals and upgrade differences (``by`` = who caused it)."""
        start, end = month_bounds(self._now_fn())
        return list(self._charges.find({"owner.kind": V.OWNER_TEAM, "owner.id": team_id,
                                        "at": {"$gte": start, "$lt": end}}))

    def _over_limit(self, actor: Actor, team_id: str, plan: Plan) -> Optional[str]:
        limits = self._teams.get(team_id).get("limits", {})
        fees = self._month_fees(team_id)
        team_spent = sum(int(f["amount_cents"]) for f in fees)
        mine = sum(int(f["amount_cents"]) for f in fees if f.get("by") == actor.user_id)
        if limits.get("per_admin_cents") is not None and mine + plan.monthly_fee_cents > limits["per_admin_cents"]:
            return "your monthly limit for this team"
        if limits.get("team_cents") is not None and team_spent + plan.monthly_fee_cents > limits["team_cents"]:
            return "the team's monthly limit"
        return None

    def _record_purchase(self, actor: Actor, request: PurchaseRequest, plan: Plan, status: str,
                         ve_id: Optional[str] = None) -> Dict[str, Any]:
        doc = {"_id": f"pur-{uuid.uuid4().hex[:12]}", "team_id": request.team_id, "buyer_id": actor.user_id,
               "buyer_name": actor.username, "plan_id": plan.plan_id, "fee_cents": plan.monthly_fee_cents,
               "month": month_of(self._now_fn()), "status": status, "request": request.to_dict(),
               "ve_id": ve_id, "created_at": self._now_fn()}
        self._purchases.insert_one(doc)
        return doc

    def _make_pending(self, actor: Actor, request: PurchaseRequest, plan: Plan, over: str) -> Dict[str, Any]:
        doc = self._record_purchase(actor, request, plan, "pending")
        team = self._teams.get(request.team_id)  # type: ignore[arg-type]
        self._notifications.notify([team["owner_id"]], N.PURCHASE_PENDING,
                                   f"{actor.username} wants to buy VE {request.name} ({plan.name}); "
                                   f"it is over {over}", {"purchase_id": doc["_id"], "team_id": request.team_id})
        return {"status": "pending", "purchase_id": doc["_id"], "reason": f"over {over}"}

    def purchases(self, actor: Actor, team_id: str, status: Optional[str] = None) -> List[Dict[str, Any]]:
        self._teams.require(actor, team_id, R.ADMIN)
        query: Dict[str, Any] = {"team_id": team_id}
        if status:
            query["status"] = status
        return [{**{k: v for k, v in d.items() if k != "_id"}, "purchase_id": d["_id"]}
                for d in self._purchases.find(query).sort("created_at", -1)]

    def spending(self, actor: Actor, team_id: str) -> Dict[str, Any]:
        """This calendar month's plan fees of the team's VEs (purchases,
        renewals, upgrades), in total and by who caused them. Team Owner and
        Team Admins (upgrade.md 13.6); only the Owner sets the limits."""
        self._teams.require(actor, team_id, R.ADMIN)
        by_buyer: Dict[str, int] = {}
        for fee in self._month_fees(team_id):
            by_buyer[fee.get("by", "")] = by_buyer.get(fee.get("by", ""), 0) + int(fee["amount_cents"])
        return {"month": month_of(self._now_fn()), "team_cents": sum(by_buyer.values()),
                "by_buyer_cents": by_buyer, "limits": self._teams.get(team_id).get("limits", {})}

    def answer_purchase(self, actor: Actor, purchase_id: str, approve: bool) -> Dict[str, Any]:
        doc = self._purchases.find_one({"_id": purchase_id})
        if doc is None or doc["status"] != "pending":
            raise NotFoundError("no such pending purchase")
        self._teams.require(actor, doc["team_id"], R.OWNER)
        if not approve:
            self._purchases.update_one({"_id": purchase_id}, {"$set": {"status": "rejected", "answered_by": actor.user_id}})
            self._notifications.notify([doc["buyer_id"]], N.PURCHASE_ANSWERED,
                                       f"Your purchase of VE {doc['request']['name']} was rejected", {"purchase_id": purchase_id})
            return {"status": "rejected"}
        request = PurchaseRequest(**doc["request"])
        plan = self._catalog.get(request.plan_id)
        buyer = Actor(user_id=doc["buyer_id"], username=doc["buyer_name"], platform_role=R.PLATFORM_USER)
        shape = self._shape(plan, request)
        team = self._teams.get(request.team_id)  # type: ignore[arg-type]
        ve = self._create(buyer, Owner(V.OWNER_TEAM, team["team_id"], team["name"]), plan, shape, request.name)
        self._purchases.update_one({"_id": purchase_id}, {"$set": {"status": "approved", "ve_id": ve.ve_id,
                                                                    "answered_by": actor.user_id}})
        self._notifications.notify([doc["buyer_id"]], N.PURCHASE_ANSWERED,
                                   f"Your purchase of VE {ve.name} was approved", {"ve_id": ve.ve_id})
        return {"status": "created", "ve": ve.public()}

    # ── plan / quota changes (Owner) ─────────────────────────────────────────
    def change_plan(self, actor: Actor, ve_id: str, plan_id: Optional[str] = None,
                    quota_cpu_cores: Optional[float] = None, quota_memory_mb: Optional[int] = None) -> Dict[str, Any]:
        """A plan with a higher fee than this period was paid at applies at
        once and pays the difference; a lower fee waits for the next renewal
        (``pending_change``). Same fee (e.g. only the quota): at once."""
        self._authz.require_ve(actor, ve_id, R.OWNER)
        ve = self.record(ve_id)
        if ve.rental_ended_at is not None:
            raise ConflictError("the rental of this VE has ended; resume it first", code="RENTAL_ENDED")
        plan = self._catalog.get(plan_id or ve.plan_id)
        quota = self._check_change(ve, plan, quota_cpu_cores, quota_memory_mb)
        now = self._now_fn()
        if plan.monthly_fee_cents < ve.period_fee_cents:
            change = {"plan_id": plan.plan_id, "quota": quota.to_dict(), "by": actor.user_id, "at": now}
            self._col.update_one({"_id": ve_id}, {"$set": {"pending_change": change}, "$inc": {"version": 1}})
            self._history.append(f"ve-{ve_id}", "VirtualEnvironmentPlanChangeScheduled",
                                 {"ve_id": ve_id, "from_plan": ve.plan_id, "to_plan": plan.plan_id,
                                  "quota": quota.to_dict(), "applies_at": ve.period_end, "by": actor.user_id,
                                  "at": now})
            self._notify_downgrade(ve, plan)
            return {**self.record(ve_id).public(), "applies": "at_renewal"}
        self._apply_plan(ve, plan, quota, actor.user_id)
        difference = plan.monthly_fee_cents - ve.period_fee_cents
        if difference > 0:
            self._col.update_one({"_id": ve_id}, {"$set": {"period_fee_cents": plan.monthly_fee_cents}})
            self._charge(ve, ve.period_index, "upgrade", plan, difference, actor.user_id,
                         f"{ve_id}:{ve.period_index}:upgrade:{ve.version}")
        return {**self.record(ve_id).public(), "applies": "now"}

    def _check_change(self, ve: VirtualEnvironment, plan: Plan, quota_cpu_cores: Optional[float],
                      quota_memory_mb: Optional[int]) -> Resources:
        if ve.owner.kind == V.OWNER_TEAM and plan.personal_only:
            raise InvalidError(f"the {plan.name} plan is personal only")
        if plan.one_per_user and plan.plan_id != ve.plan_id and self._col.count_documents(
                {"owner.kind": V.OWNER_USER, "owner.id": ve.owner.id, "plan_id": plan.plan_id,
                 "desired": {"$ne": V.DESIRED_DELETED}, "_id": {"$ne": ve.ve_id}}):
            raise ConflictError(f"you already have a {plan.name} VE (one per person)", code="ONE_FREE_VE")
        quota = Resources(float(quota_cpu_cores if quota_cpu_cores is not None else ve.quota.cpu_cores),
                          int(quota_memory_mb if quota_memory_mb is not None else ve.quota.memory_mb))
        if plan.fixed_endpoint_size:
            quota = ve.endpoint_size.times(plan.max_endpoints)
        # A downgrade lowers the target to the new maximum (the extra
        # endpoints drain); the quota must still hold what remains.
        target = min(int((ve.heartbeat or {}).get("target_endpoints", ve.target_endpoints)), plan.max_endpoints)
        check_shape(plan, VeShape(quota=quota, endpoint_size=ve.endpoint_size,
                                  endpoint_size_name=ve.endpoint_size_name, target_endpoints=target,
                                  min_endpoints=min(ve.min_endpoints, target)))
        return quota

    def _apply_plan(self, ve: VirtualEnvironment, plan: Plan, quota: Resources, by: str) -> None:
        reserved = quota + plan.base
        self._capacity.change(ve.reserved, reserved)
        self._col.update_one({"_id": ve.ve_id}, {"$set": {"plan_id": plan.plan_id, "quota": quota.to_dict(),
                                                          "reserved": reserved.to_dict(), "limits_dirty": True,
                                                          "pending_change": None},
                                                 "$inc": {"version": 1}})
        self._history.append(f"ve-{ve.ve_id}", "VirtualEnvironmentPlanChanged",
                             {"ve_id": ve.ve_id, "from_plan": ve.plan_id, "to_plan": plan.plan_id,
                              "quota": quota.to_dict(), "by": by, "at": self._now_fn()})

    # ── rental: cancel / resume / renewals ───────────────────────────────────
    def cancel(self, actor: Actor, ve_id: str) -> Dict[str, Any]:
        """No more renewals: the VE works until the period ends, then stops."""
        self._authz.require_ve(actor, ve_id, R.OWNER)
        ve = self.record(ve_id)
        if ve.desired == V.DESIRED_DELETED or ve.rental_ended_at is not None:
            raise ConflictError("this VE's rental already ended")
        if not ve.cancel_at_period_end:
            self._col.update_one({"_id": ve_id}, {"$set": {"cancel_at_period_end": True}, "$inc": {"version": 1}})
            self._history.append(f"ve-{ve_id}", "VirtualEnvironmentRentalCancelled",
                                 {"ve_id": ve_id, "ends_at": ve.period_end, "by": actor.user_id,
                                  "at": self._now_fn()})
            ends = ve.period_end or self._now_fn()
            self._notifications.notify(
                N.bill_viewers(self._db, ve.owner.to_dict()), N.RENTAL_ENDING,
                f"{ve.name} stops on {{ends_at}}; data kept until {{delete_after}}",
                {"ve_id": ve_id, "ends_at": ends, "delete_after": ends + self._grace_s, "cancelled": True},
                dedup_key=f"cancel:{ve_id}:{ve.period_index}",
                fields={"ends_at": S.date(ends), "delete_after": S.date(ends + self._grace_s)})
        return self.record(ve_id).public()

    def _notify_downgrade(self, ve: VirtualEnvironment, plan: Plan) -> None:
        """What a scheduled downgrade will take away, said up front."""
        old = self._catalog.get(ve.plan_id)
        target = int((ve.heartbeat or {}).get("target_endpoints", ve.target_endpoints))
        losses = []
        if target > plan.max_endpoints:
            extra = target - plan.max_endpoints
            losses.append(f"{extra} endpoint{'s' if extra != 1 else ''} will be removed")
        if plan.max_retention_days < old.max_retention_days:
            losses.append(f"history older than {plan.max_retention_days} days will no longer be shown")
        if plan.object_max_state_bytes < old.object_max_state_bytes:
            losses.append(f"an object may hold at most {_size(plan.object_max_state_bytes)} of state "
                          "(calls that would make it bigger fail)")
        if plan.object_max_keep_revisions < old.object_max_keep_revisions:
            losses.append(f"objects keep at most {plan.object_max_keep_revisions} past revisions")
        text = f"{ve.name} moves to {plan.name} on {{applies_at}}"
        self._notifications.notify(
            N.bill_viewers(self._db, ve.owner.to_dict()), N.PLAN_CHANGE_SCHEDULED,
            text + (": " + ", ".join(losses) if losses else ""),
            {"ve_id": ve.ve_id, "to_plan": plan.plan_id, "applies_at": ve.period_end,
             "endpoints_removed": max(0, target - plan.max_endpoints),
             "retention_days": plan.max_retention_days if plan.max_retention_days < old.max_retention_days
             else None},
            dedup_key=f"downgrade:{ve.ve_id}:{ve.period_index}:{plan.plan_id}:{ve.version}",
            fields={"applies_at": S.date(ve.period_end)})

    # ── budget (Owner; warnings only) ────────────────────────────────────────
    def set_budget(self, actor: Actor, ve_id: str, budget_cents: Optional[int]) -> Dict[str, Any]:
        """A per-period budget: notices at 80 % and 100 % of it (jobs are
        never stopped). None removes it."""
        self._authz.require_ve(actor, ve_id, R.OWNER)
        if budget_cents is not None and int(budget_cents) <= 0:
            raise InvalidError("a budget must be a positive amount of cents (or none)")
        self.record(ve_id)
        self._col.update_one({"_id": ve_id}, {"$set": {"budget_cents": None if budget_cents is None
                                                       else int(budget_cents)}, "$inc": {"version": 1}})
        self._history.append(f"ve-{ve_id}", "VirtualEnvironmentBudgetSet",
                             {"ve_id": ve_id, "budget_cents": budget_cents, "by": actor.user_id,
                              "at": self._now_fn()})
        return self.record(ve_id).public()

    def resume(self, actor: Actor, ve_id: str) -> Dict[str, Any]:
        """Before the period ends: undo the cancel. After (stopped, in the
        grace period): rent it again -- a new period starts now, its fee is
        charged, and the VE starts."""
        self._authz.require_ve(actor, ve_id, R.OWNER)
        ve = self.record(ve_id)
        if ve.desired == V.DESIRED_DELETED:
            raise ConflictError("the VE is being deleted")
        now = self._now_fn()
        if ve.rental_ended_at is None:
            if ve.cancel_at_period_end:
                self._col.update_one({"_id": ve_id}, {"$set": {"cancel_at_period_end": False},
                                                      "$inc": {"version": 1}})
                self._history.append(f"ve-{ve_id}", "VirtualEnvironmentRentalResumed",
                                     {"ve_id": ve_id, "by": actor.user_id, "at": now})
            return self.record(ve_id).public()
        plan = self._catalog.get(ve.plan_id)
        index = ve.period_index + 1
        start, end = rental_period(now, index, index)
        updated = self._col.update_one(
            {"_id": ve_id, "period_index": ve.period_index, "rental_ended_at": {"$ne": None}},
            {"$set": {"period_anchor": now, "period_anchor_index": index, "period_index": index,
                      "period_start": start, "period_end": end, "period_fee_cents": plan.monthly_fee_cents,
                      "cancel_at_period_end": False, "rental_ended_at": None, "rental_delete_after": None,
                      "desired": V.DESIRED_RUNNING, "lifecycle": V.STARTING,
                      "lifecycle_reason": f"rented again by {actor.username}", "step": "", "step_error": ""},
             "$inc": {"version": 1}})
        if updated.modified_count:
            self._charge(self.record(ve_id), index, "plan_fee", plan, plan.monthly_fee_cents, actor.user_id,
                         f"{ve_id}:{index}:fee")
            self._history.append(f"ve-{ve_id}", "VirtualEnvironmentRentedAgain",
                                 {"ve_id": ve_id, "period_start": start, "period_end": end, "by": actor.user_id,
                                  "at": now})
        return self.record(ve_id).public()

    def renew_due(self) -> List[str]:
        """Run by one CP copy at a time (lease). For every VE whose period has
        ended: renew it (apply a scheduled downgrade first, charge the new
        period's fee) -- or, if cancelled, stop it and keep its data for the
        grace period; delete it once that is over. Safe to repeat."""
        now = self._now_fn()
        done: List[str] = []
        for doc in self._col.find({"desired": {"$ne": V.DESIRED_DELETED}, "rental_ended_at": None,
                                   "period_end": {"$lte": now}}):
            ve = VirtualEnvironment.from_doc(doc)
            while ve.period_end is not None and ve.period_end <= now and ve.rental_ended_at is None:
                ve = self._end_period(ve, now)
            done.append(ve.ve_id)
        for doc in self._col.find({"desired": {"$ne": V.DESIRED_DELETED}, "rental_delete_after": {"$lte": now}}):
            self._col.update_one({"_id": doc["_id"], "desired": {"$ne": V.DESIRED_DELETED}},
                                 {"$set": {"desired": V.DESIRED_DELETED, "lifecycle": V.DELETING,
                                           "lifecycle_reason": "rental ended and the grace period is over",
                                           "step": "", "step_error": ""}, "$inc": {"version": 1}})
            self._history.append(f"ve-{doc['_id']}", "VirtualEnvironmentDesiredChanged",
                                 {"ve_id": doc["_id"], "desired": V.DESIRED_DELETED, "by": "rental", "at": now})
            done.append(doc["_id"])
        return done

    def _end_period(self, ve: VirtualEnvironment, now: float) -> VirtualEnvironment:
        guard = {"_id": ve.ve_id, "period_index": ve.period_index, "rental_ended_at": None}
        if ve.cancel_at_period_end:
            changes: Dict[str, Any] = {"rental_ended_at": ve.period_end,
                                       "rental_delete_after": (ve.period_end or now) + self._grace_s}
            if ve.desired in (V.DESIRED_RUNNING, V.DESIRED_SLEEPING):
                changes.update({"desired": V.DESIRED_STOPPED, "lifecycle": V.STOPPING,
                                "lifecycle_reason": "rental cancelled and the period ended", "step": "",
                                "step_error": ""})
            if self._col.update_one(guard, {"$set": changes, "$inc": {"version": 1}}).modified_count:
                self._history.append(f"ve-{ve.ve_id}", "VirtualEnvironmentRentalEnded",
                                     {"ve_id": ve.ve_id, "ended_at": ve.period_end,
                                      "delete_after": changes["rental_delete_after"], "at": now})
            return self.record(ve.ve_id)
        if ve.pending_change:
            self._apply_pending(ve)
            ve = self.record(ve.ve_id)
        plan = self._catalog.get(ve.plan_id)
        index = ve.period_index + 1
        start, end = rental_period(ve.period_anchor if ve.period_anchor is not None else ve.created_at,
                                   ve.period_anchor_index, index)
        if self._col.update_one(guard, {"$set": {"period_index": index, "period_start": start, "period_end": end,
                                                 "period_fee_cents": plan.monthly_fee_cents},
                                        "$inc": {"version": 1}}).modified_count:
            self._charge(self.record(ve.ve_id), index, "plan_fee", plan, plan.monthly_fee_cents, "renewal",
                         f"{ve.ve_id}:{index}:fee")
            self._history.append(f"ve-{ve.ve_id}", "VirtualEnvironmentRenewed",
                                 {"ve_id": ve.ve_id, "period_index": index, "period_start": start,
                                  "period_end": end, "plan_id": plan.plan_id, "fee_cents": plan.monthly_fee_cents,
                                  "at": now})
        return self.record(ve.ve_id)

    def _apply_pending(self, ve: VirtualEnvironment) -> None:
        """The scheduled downgrade, checked again (things may have changed
        since it was asked for); if it no longer fits, the plan stays."""
        change = ve.pending_change or {}
        try:
            plan = self._catalog.get(change["plan_id"])
            quota = Resources.from_dict(change.get("quota"))
            self._check_change(ve, plan, quota.cpu_cores, quota.memory_mb)
            self._apply_plan(ve, plan, quota, change.get("by", "renewal"))
        except Exception as exc:          # noqa: BLE001 -- recorded, the renewal goes on
            self._col.update_one({"_id": ve.ve_id}, {"$set": {"pending_change": None}})
            self._history.append(f"ve-{ve.ve_id}", "VirtualEnvironmentPlanChangeFailed",
                                 {"ve_id": ve.ve_id, "to_plan": change.get("plan_id"), "error": str(exc)[:300],
                                  "at": self._now_fn()})

    def _charge(self, ve: VirtualEnvironment, index: int, kind: str, plan: Plan, amount_cents: int, by: str,
                charge_id: str) -> None:
        """One fee line of a period (``plan_fee`` or ``upgrade``); the fixed
        id makes a repeat a no-op."""
        try:
            self._charges.insert_one({
                "_id": charge_id, "ve_id": ve.ve_id, "owner": ve.owner.to_dict(), "period_index": index,
                "period_start": ve.period_start, "period_end": ve.period_end, "kind": kind,
                "plan_id": plan.plan_id, "amount_cents": int(amount_cents), "by": by, "at": self._now_fn()})
        except DuplicateKeyError:
            pass

    def charges(self, ve_id: str, period_index: Optional[int] = None) -> List[Dict[str, Any]]:
        query: Dict[str, Any] = {"ve_id": ve_id}
        if period_index is not None:
            query["period_index"] = period_index
        return [{**{k: v for k, v in d.items() if k != "_id"}, "charge_id": d["_id"]}
                for d in self._charges.find(query).sort("at", 1)]

    # ── stop / start / delete (desired state) ────────────────────────────────
    def stop(self, actor: Actor, ve_id: str) -> Dict[str, Any]:
        self._authz.require_ve(actor, ve_id, R.ADMIN)
        return self._set_desired(ve_id, V.DESIRED_STOPPED, V.STOPPING, actor)

    def repair(self, actor: Actor, ve_id: str) -> Dict[str, Any]:
        """Check every piece of the VE and fix what can be fixed (VeRepair);
        Admins, once every few minutes per VE."""
        self._authz.require_ve(actor, ve_id, R.ADMIN)
        if self.repairs is None:
            raise ConflictError("repairs aren't available here")
        return self.repairs.repair(ve_id, actor.user_id, actor.username)

    def repair_status(self, actor: Actor, ve_id: str) -> Dict[str, Any]:
        self._authz.require_ve(actor, ve_id, R.VIEWER)
        if self.repairs is None:
            return {"last_repair": None, "available_at": None, "every_s": None}
        return self.repairs.status(ve_id)

    def start(self, actor: Actor, ve_id: str) -> Dict[str, Any]:
        self._authz.require_ve(actor, ve_id, R.ADMIN)
        ve = self.record(ve_id)
        if ve.desired == V.DESIRED_DELETED:
            raise ConflictError("the VE is being deleted")
        if ve.rental_ended_at is not None:
            raise ConflictError("the rental of this VE has ended; the Owner can resume it", code="RENTAL_ENDED")
        return self._set_desired(ve_id, V.DESIRED_RUNNING, V.STARTING, actor)

    def delete(self, actor: Actor, ve_id: str, confirm_name: str) -> Dict[str, Any]:
        self._authz.require_ve(actor, ve_id, R.OWNER)
        ve = self.record(ve_id)
        if (confirm_name or "").strip().lower() != ve.name:
            raise InvalidError("type the VE's name to confirm deleting it", code="CONFIRMATION_REQUIRED")
        return self._set_desired(ve_id, V.DESIRED_DELETED, V.DELETING, actor)

    def _set_desired(self, ve_id: str, desired: str, lifecycle: str, actor: Actor) -> Dict[str, Any]:
        ve = self.record(ve_id)
        if ve.desired == desired:
            return ve.public()
        if ve.desired == V.DESIRED_DELETED:
            raise ConflictError("the VE is being deleted")
        self._col.update_one({"_id": ve_id}, {"$set": {"desired": desired, "lifecycle": lifecycle,
                                                       "lifecycle_reason": f"requested by {actor.username}",
                                                       "step": "", "step_error": ""},
                                              "$inc": {"version": 1}})
        self._history.append(f"ve-{ve_id}", "VirtualEnvironmentDesiredChanged",
                             {"ve_id": ve_id, "desired": desired, "by": actor.user_id, "at": self._now_fn()})
        return self.record(ve_id).public()


def _size(n: int) -> str:
    for unit, size in (("GB", 1024 ** 3), ("MB", 1024 ** 2), ("KB", 1024)):
        if n >= size:
            return f"{n / size:g} {unit}"
    return f"{n} bytes"
