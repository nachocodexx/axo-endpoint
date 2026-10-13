from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from pymongo.database import Database

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
from axo_control_plane.domain import virtual_environment as V
from axo_control_plane.domain.errors import ConflictError, ForbiddenError, InvalidError, NotFoundError
from axo_control_plane.domain.names import check_ve_name, new_ve_id
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
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m")


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
    """

    def __init__(self, db: Database, catalog: PlanCatalog, capacity: CapacityLedger, identity: IdentityProvider,
                 access: AccessResolver, authz: Authorizer, teams: Teams, notifications: Notifications,
                 history: HistoryLog, max_custom: Optional[Resources] = None,
                 now_fn: Callable[[], float] = time.time) -> None:
        self._col = db["virtual_environments"]
        self._purchases = db["purchases"]
        self._catalog = catalog
        self._capacity = capacity
        self._identity = identity
        self._access = access
        self._authz = authz
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
        return {**ve.public(), "my_role": role}

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
            out.append({**ve.public(), "my_role": self._authz.ve_role(actor, ve.ve_id)})
        return out

    # ── buying ───────────────────────────────────────────────────────────────
    def buy(self, actor: Actor, request: PurchaseRequest) -> Dict[str, Any]:
        if actor.is_guest or actor.key_id is not None:
            raise ForbiddenError("only a signed-in user (not a guest or an SDK key) can buy a VE")
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
        ve = VirtualEnvironment(
            ve_id=new_ve_id(name), name=name, owner=owner, plan_id=plan.plan_id, quota=shape.quota,
            endpoint_size_name=shape.endpoint_size_name, endpoint_size=shape.endpoint_size,
            target_endpoints=shape.target_endpoints, min_endpoints=shape.min_endpoints, reserved=reserved,
            created_by=actor.user_id, created_at=now, desired=V.DESIRED_RUNNING, lifecycle=V.CREATING,
        )
        try:
            self._col.insert_one(ve.to_doc())
            self._give_ownership(ve)
        except Exception:
            self._capacity.release(reserved)
            self._col.delete_one({"_id": ve.ve_id})
            raise
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

    # ── team spending limits ─────────────────────────────────────────────────
    def _over_limit(self, actor: Actor, team_id: str, plan: Plan) -> Optional[str]:
        limits = self._teams.get(team_id).get("limits", {})
        month = month_of(self._now_fn())
        approved = list(self._purchases.find({"team_id": team_id, "month": month, "status": "approved"}))
        team_spent = sum(p["fee_cents"] for p in approved)
        mine = sum(p["fee_cents"] for p in approved if p["buyer_id"] == actor.user_id)
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
        self._teams.require(actor, team_id, R.OWNER)
        month = month_of(self._now_fn())
        approved = list(self._purchases.find({"team_id": team_id, "month": month, "status": "approved"}))
        by_admin: Dict[str, int] = {}
        for p in approved:
            by_admin[p["buyer_id"]] = by_admin.get(p["buyer_id"], 0) + p["fee_cents"]
        return {"month": month, "team_cents": sum(by_admin.values()), "by_buyer_cents": by_admin,
                "limits": self._teams.get(team_id).get("limits", {})}

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
        self._authz.require_ve(actor, ve_id, R.OWNER)
        ve = self.record(ve_id)
        plan = self._catalog.get(plan_id or ve.plan_id)
        if ve.owner.kind == V.OWNER_TEAM and plan.personal_only:
            raise InvalidError(f"the {plan.name} plan is personal only")
        target = int((ve.heartbeat or {}).get("target_endpoints", ve.target_endpoints))
        quota = Resources(float(quota_cpu_cores if quota_cpu_cores is not None else ve.quota.cpu_cores),
                          int(quota_memory_mb if quota_memory_mb is not None else ve.quota.memory_mb))
        if plan.fixed_endpoint_size:
            quota = ve.endpoint_size.times(plan.max_endpoints)
        # A downgrade lowers the target to the new maximum (the extra
        # endpoints drain); the quota must still hold what remains.
        target = min(target, plan.max_endpoints)
        check_shape(plan, VeShape(quota=quota, endpoint_size=ve.endpoint_size,
                                  endpoint_size_name=ve.endpoint_size_name, target_endpoints=target,
                                  min_endpoints=min(ve.min_endpoints, target)))
        reserved = quota + plan.base
        self._capacity.change(ve.reserved, reserved)
        self._col.update_one({"_id": ve_id}, {"$set": {"plan_id": plan.plan_id, "quota": quota.to_dict(),
                                                       "reserved": reserved.to_dict(), "limits_dirty": True},
                                              "$inc": {"version": 1}})
        self._history.append(f"ve-{ve_id}", "VirtualEnvironmentPlanChanged",
                             {"ve_id": ve_id, "from_plan": ve.plan_id, "to_plan": plan.plan_id,
                              "quota": quota.to_dict(), "by": actor.user_id, "at": self._now_fn()})
        return self.record(ve_id).public()

    # ── stop / start / delete (desired state) ────────────────────────────────
    def stop(self, actor: Actor, ve_id: str) -> Dict[str, Any]:
        self._authz.require_ve(actor, ve_id, R.ADMIN)
        return self._set_desired(ve_id, V.DESIRED_STOPPED, V.STOPPING, actor)

    def start(self, actor: Actor, ve_id: str) -> Dict[str, Any]:
        self._authz.require_ve(actor, ve_id, R.ADMIN)
        ve = self.record(ve_id)
        if ve.desired == V.DESIRED_DELETED:
            raise ConflictError("the VE is being deleted")
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
