from __future__ import annotations

import hashlib
import secrets
import time
import uuid
from typing import Any, Callable, Dict, List, Optional

from pymongo.database import Database

from axo_control_plane.application import notifications as N
from axo_control_plane.application.access import AccessResolver
from axo_control_plane.application.authz import Authorizer
from axo_control_plane.application.buckets import Buckets
from axo_control_plane.application.notifications import Notifications
from axo_control_plane.application.people import Actor, Names, People
from axo_control_plane.application.ports import HistoryLog, IdentityProvider
from axo_control_plane.application.teams import Teams
from axo_control_plane.domain import roles as R
from axo_control_plane.domain import virtual_environment as V
from axo_control_plane.domain.errors import ConflictError, ForbiddenError, InvalidError, NotFoundError

PENDING = "pending"
ACCEPTED = "accepted"
REJECTED = "rejected"
CANCELLED = "cancelled"

KIND_VE = "ve"
KIND_BUCKET = "bucket"
KIND_TEAM = "team"


class Sharing:
    """Invitations (VE / bucket / team access) and ownership transfers.

    - Every invitation must be accepted: by the person, or for a team by its
      Owner / an Admin. Only then does Xolo get the grant.
    - Who may give which VE role: only the Owner gives Admin; Owner and
      Admin give Developer / Viewer. A guest account is only ever a Viewer
      (or a bucket Reader).
    - Someone without an account can be invited by email: the invitation
      carries a one-time code; signing up with it makes a guest account and
      accepts the invitation.
    - A transfer is requested (= confirmed) by the current owner and done
      only when the receiver (the person, or the Team Owner) accepts.
    """

    def __init__(self, db: Database, people: People, names: Names, teams: Teams, buckets: Buckets,
                 identity: IdentityProvider, access: AccessResolver, authz: Authorizer,
                 notifications: Notifications, history: HistoryLog, now_fn: Callable[[], float] = time.time) -> None:
        self._invites = db["invitations"]
        self._transfers = db["transfers"]
        self._ves = db["virtual_environments"]
        self._plans = db["plans"]
        self._people = people
        self._names = names
        self._teams = teams
        self._buckets = buckets
        self._identity = identity
        self._access = access
        self._authz = authz
        self._notifications = notifications
        self._history = history
        self._now_fn = now_fn

    # ── who is invited ───────────────────────────────────────────────────────
    def _invitee(self, who: str) -> Dict[str, Any]:
        """A username, a team name, or an email of someone without an account."""
        who = (who or "").strip().lower()
        user = self._people.find(who)
        if user is not None:
            return {"type": "user", "id": user["user_id"], "name": user["username"],
                    "guest": user.get("platform_role") == R.PLATFORM_GUEST}
        entry = self._names.lookup(who)
        if entry is not None and entry["kind"] == "team":
            return {"type": "team", "id": entry["id"], "name": who, "guest": False}
        if "@" in who and "." in who.split("@")[-1]:
            return {"type": "email", "id": who, "name": who, "guest": True}
        raise NotFoundError(f"no user or team called {who!r}")

    # ── invitations ──────────────────────────────────────────────────────────
    def invite_to_ve(self, actor: Actor, ve_id: str, who: str, role: str) -> Dict[str, Any]:
        R.check_ve_role(role)
        mine = self._authz.require_ve(actor, ve_id, R.ADMIN)
        if not R.may_assign_ve_role(mine, role):
            raise ForbiddenError("only the VE Owner gives the Admin role")
        invitee = self._invitee(who)
        if invitee["guest"] and role != R.VIEWER:
            raise InvalidError("a guest can only be a Viewer")
        ve = self._ves.find_one({"_id": ve_id})
        return self._create(actor, KIND_VE, ve_id, ve["name"], role, invitee)

    def invite_to_bucket(self, actor: Actor, full_name: str, who: str, role: str) -> Dict[str, Any]:
        R.check_bucket_role(role)
        doc = self._buckets.resolve(full_name)
        self._authz.require_bucket(actor, doc["_id"], R.OWNER)
        invitee = self._invitee(who)
        if invitee["guest"] and role != R.READER:
            raise InvalidError("a guest can only be a Reader")
        return self._create(actor, KIND_BUCKET, doc["_id"], doc["full_name"], role, invitee)

    def invite_to_team(self, actor: Actor, team_id: str, who: str, role: str = R.MEMBER) -> Dict[str, Any]:
        if role not in (R.MEMBER, R.ADMIN):
            raise InvalidError("a team role is member or admin")
        mine = self._teams.require(actor, team_id, R.ADMIN)
        if role == R.ADMIN and mine != R.OWNER:
            raise ForbiddenError("only the Team Owner invites admins")
        invitee = self._invitee(who)
        if invitee["type"] == "team":
            raise InvalidError("teams can't join teams")
        if invitee["guest"]:
            raise InvalidError("guest accounts can't join teams")
        team = self._teams.get(team_id)
        return self._create(actor, KIND_TEAM, team_id, team["name"], role, invitee)

    def _create(self, actor: Actor, kind: str, target_id: str, target_name: str, role: str,
                invitee: Dict[str, Any]) -> Dict[str, Any]:
        if self._invites.find_one({"kind": kind, "target_id": target_id, "invitee.id": invitee["id"],
                                   "status": PENDING}):
            raise ConflictError("there is already a pending invitation for them")
        doc: Dict[str, Any] = {
            "_id": f"inv-{uuid.uuid4().hex[:16]}", "kind": kind, "target_id": target_id,
            "target_name": target_name, "role": role,
            "invitee": {k: invitee[k] for k in ("type", "id", "name")},
            "invited_by": actor.user_id, "invited_by_name": actor.username,
            "status": PENDING, "created_at": self._now_fn(),
        }
        code = None
        if invitee["type"] == "email":
            code = secrets.token_urlsafe(24)
            doc["code_hash"] = _hash(code)
        self._invites.insert_one(doc)
        self._notifications.notify(self._answerers(doc), N.INVITATION_RECEIVED,
                                   f"{actor.username} invited you to {kind} {target_name} as {role}",
                                   {"invitation_id": doc["_id"]})
        self._history.append(f"{kind}-{target_id}", "InvitationSent",
                             {"invitation_id": doc["_id"], "role": role, "invitee": doc["invitee"],
                              "by": actor.user_id, "at": self._now_fn()})
        out = _public(doc)
        if code is not None:
            # Shown once to the inviter (there is no email sending yet).
            out["signup_code"] = code
        return out

    def _answerers(self, doc: Dict[str, Any]) -> List[str]:
        invitee = doc["invitee"]
        if invitee["type"] == "user":
            return [invitee["id"]]
        if invitee["type"] == "team":
            team = self._teams.get(invitee["id"])
            return [m["user_id"] for m in team["members"] if m["role"] in (R.OWNER, R.ADMIN)]
        return []

    def _may_answer(self, actor: Actor, doc: Dict[str, Any]) -> bool:
        invitee = doc["invitee"]
        if invitee["type"] == "user":
            return invitee["id"] == actor.user_id
        if invitee["type"] == "team":
            return R.at_least(self._teams.role_of(actor.user_id, invitee["id"]), R.ADMIN, R.TEAM_ROLES)
        return False

    def incoming(self, actor: Actor) -> List[Dict[str, Any]]:
        team_ids = [t["team_id"] for t in self._teams.of_user(actor.user_id)
                    if R.at_least(self._teams.role_of(actor.user_id, t["team_id"]), R.ADMIN, R.TEAM_ROLES)]
        query = {"status": PENDING, "$or": [{"invitee.id": actor.user_id}, {"invitee.id": {"$in": team_ids}}]}
        return [_public(d) for d in self._invites.find(query).sort("created_at", -1)]

    def sent(self, actor: Actor) -> List[Dict[str, Any]]:
        """Invitations this person sent that wait for an answer (the inbox's "Sent")."""
        return [_public(d) for d in self._invites.find({"invited_by": actor.user_id, "status": PENDING})
                .sort("created_at", -1)]

    def answered(self, actor: Actor, days: int = 30) -> List[Dict[str, Any]]:
        """Invitations answered or cancelled in the last ``days`` that this
        person sent, received, or answered for one of their teams."""
        team_ids = [t["team_id"] for t in self._teams.of_user(actor.user_id)]
        query = {"status": {"$ne": PENDING}, "answered_at": {"$gte": self._now_fn() - days * 86400},
                 "$or": [{"invited_by": actor.user_id}, {"invitee.id": actor.user_id},
                         {"invitee.id": {"$in": team_ids}}]}
        return [_public(d) for d in self._invites.find(query).sort("answered_at", -1).limit(200)]

    def outgoing(self, kind: str, target_id: str) -> List[Dict[str, Any]]:
        return [_public(d) for d in self._invites.find({"kind": kind, "target_id": target_id, "status": PENDING})]

    def answer(self, actor: Actor, invitation_id: str, accept: bool) -> Dict[str, Any]:
        doc = self._invites.find_one({"_id": invitation_id, "status": PENDING})
        if doc is None:
            raise NotFoundError("no such pending invitation")
        if not self._may_answer(actor, doc):
            raise ForbiddenError("this invitation is not for you")
        return self._finish(actor, doc, accept)

    def accept_with_code(self, actor: Actor, code: str) -> Dict[str, Any]:
        """Right after a guest signed up with the code from an email invitation."""
        doc = self._invites.find_one({"code_hash": _hash(code), "status": PENDING})
        if doc is None:
            raise NotFoundError("this invitation code is not valid any more")
        self._invites.update_one({"_id": doc["_id"]}, {"$set": {"invitee": {"type": "user", "id": actor.user_id,
                                                                            "name": actor.username}},
                                                       "$unset": {"code_hash": ""}})
        doc = self._invites.find_one({"_id": doc["_id"]})
        return self._finish(actor, doc, accept=True)

    def invitation_for_code(self, code: str) -> Optional[Dict[str, Any]]:
        doc = self._invites.find_one({"code_hash": _hash(code), "status": PENDING})
        return _public(doc) if doc else None

    def cancel(self, actor: Actor, invitation_id: str) -> None:
        doc = self._invites.find_one({"_id": invitation_id, "status": PENDING})
        if doc is None:
            raise NotFoundError("no such pending invitation")
        if doc["invited_by"] != actor.user_id:
            self._require_manager(actor, doc["kind"], doc["target_id"])
        self._invites.update_one({"_id": invitation_id}, {"$set": {"status": CANCELLED,
                                                                  "answered_at": self._now_fn()}})

    def _require_manager(self, actor: Actor, kind: str, target_id: str) -> None:
        if kind == KIND_VE:
            self._authz.require_ve(actor, target_id, R.ADMIN)
        elif kind == KIND_BUCKET:
            self._authz.require_bucket(actor, target_id, R.OWNER)
        else:
            self._teams.require(actor, target_id, R.ADMIN)

    def _finish(self, actor: Actor, doc: Dict[str, Any], accept: bool) -> Dict[str, Any]:
        status = ACCEPTED if accept else REJECTED
        if accept:
            self._apply(doc, by=actor.user_id)
        self._invites.update_one({"_id": doc["_id"]}, {"$set": {"status": status, "answered_by": actor.user_id,
                                                               "answered_at": self._now_fn()}})
        self._notifications.notify([doc["invited_by"]], N.INVITATION_ANSWERED,
                                   f"{doc['invitee']['name']} {status} your invitation to {doc['target_name']}",
                                   {"invitation_id": doc["_id"]})
        self._history.append(f"{doc['kind']}-{doc['target_id']}", "InvitationAnswered",
                             {"invitation_id": doc["_id"], "status": status, "by": actor.user_id,
                              "at": self._now_fn()})
        return {**_public(doc), "status": status}

    def _apply(self, doc: Dict[str, Any], by: str) -> None:
        invitee, role = doc["invitee"], doc["role"]
        if doc["kind"] == KIND_TEAM:
            self._teams.add_member(doc["target_id"], invitee["id"], role, by=by)
            return
        principal_id, principal_type = invitee["id"], "USER"
        if invitee["type"] == "team":
            principal_id, principal_type = self._teams.groups(invitee["id"])["members"], "GROUP"
        if doc["kind"] == KIND_VE:
            self._set_ve_grant(doc["target_id"], principal_id, principal_type, role)
        else:
            self._buckets.share(doc["target_id"], principal_id, principal_type, role, by=by)

    # ── VE access list (the Access tab) ──────────────────────────────────────
    def _set_ve_grant(self, ve_id: str, principal_id: str, principal_type: str, role: Optional[str]) -> None:
        resource = R.ve_resource(ve_id)
        self._identity.revoke(resource, principal_id, principal_type, R.ALL_GRANTABLE)
        if role is not None:
            self._identity.grant(resource, principal_id, principal_type, R.XOLO_PERMISSIONS[role])
        self._access.changed_resource(resource)

    def ve_access(self, actor: Actor, ve_id: str) -> List[Dict[str, Any]]:
        self._authz.require_ve(actor, ve_id, R.ADMIN)
        out = []
        for grant in self._access.grants(R.ve_resource(ve_id)):
            out.append({"principal_id": grant.principal_id, "principal_type": grant.principal_type,
                        "name": self._principal_name(grant.principal_id),
                        "role": R.ve_role_from_permissions(grant.permissions, grant.is_owner)})
        return out

    def bucket_access(self, actor: Actor, full_name: str) -> List[Dict[str, Any]]:
        """Who has access and with which role: anyone with access sees it,
        only the Owner changes it."""
        doc = self._buckets.resolve(full_name)
        self._authz.require_bucket(actor, doc["_id"], R.READER)
        out = []
        for grant in self._access.grants(R.bucket_resource(doc["_id"])):
            out.append({"principal_id": grant.principal_id, "principal_type": grant.principal_type,
                        "name": self._principal_name(grant.principal_id),
                        "role": R.bucket_role_from_permissions(grant.permissions, grant.is_owner)})
        return out

    def set_bucket_role(self, actor: Actor, full_name: str, principal_id: str, principal_type: str, role: str) -> None:
        """Reader <-> Writer for someone who already has access (they accepted
        once; no new invitation). The Owner changes only by transfer."""
        R.check_bucket_role(role)
        if role == R.OWNER:
            raise InvalidError("ownership moves only by a transfer")
        doc = self._buckets.resolve(full_name)
        self._authz.require_bucket(actor, doc["_id"], R.OWNER)
        resource = R.bucket_resource(doc["_id"])
        current = next((g for g in self._access.grants(resource) if g.principal_id == principal_id), None)
        if current is None:
            raise NotFoundError("they have no access to this bucket")
        if current.is_owner:
            raise ForbiddenError("the Owner changes only by transfer")
        self._identity.revoke(resource, principal_id, principal_type, R.ALL_GRANTABLE)
        self._identity.grant(resource, principal_id, principal_type, R.XOLO_BUCKET_PERMISSIONS[role])
        self._access.changed_resource(resource)
        self._history.append(f"bucket-{doc['_id']}", "BucketRoleChanged",
                             {"bucket_id": doc["_id"], "principal_id": principal_id, "role": role,
                              "by": actor.user_id, "at": self._now_fn()})

    def _principal_name(self, principal_id: str) -> Optional[str]:
        """A person's username, or "<team> (owner | admins | members)" for a team's groups."""
        try:
            return self._people.get(principal_id)["username"]
        except NotFoundError:
            team = self._teams.of_group(principal_id)
            if team is None:
                return None
            kind = next(k for k, g in team["groups"].items() if g == principal_id)
            return f"{team['name']} ({'owner' if kind == 'owner' else kind})"

    def set_ve_role(self, actor: Actor, ve_id: str, principal_id: str, principal_type: str,
                    role: Optional[str]) -> None:
        """Change someone's role (role None = remove their access). Anyone may
        remove their own access; the Owner's is changed only by transfer."""
        resource = R.ve_resource(ve_id)
        current = next((g for g in self._access.grants(resource) if g.principal_id == principal_id), None)
        if current is None:
            raise NotFoundError("they have no access to this VE")
        if current.is_owner:
            raise ForbiddenError("the Owner changes only by transfer")
        leaving_self = role is None and principal_id == actor.user_id
        if not leaving_self:
            mine = self._authz.require_ve(actor, ve_id, R.ADMIN)
            theirs = R.ve_role_from_permissions(current.permissions)
            if role is not None:
                R.check_ve_role(role)
            for touched in (theirs, role):
                if touched == R.ADMIN and mine != R.OWNER:
                    raise ForbiddenError("only the VE Owner gives or takes the Admin role")
        self._set_ve_grant(ve_id, principal_id, principal_type, role)
        self._history.append(f"ve-{ve_id}", "VeRoleChanged", {"ve_id": ve_id, "principal_id": principal_id,
                                                              "role": role, "by": actor.user_id,
                                                              "at": self._now_fn()})

    # ── ownership transfers ──────────────────────────────────────────────────
    def request_transfer(self, actor: Actor, kind: str, target: str, to: str) -> Dict[str, Any]:
        """Asked (and so confirmed) by the current owner."""
        receiver = self._invitee(to)
        if receiver["type"] == "email" or receiver["guest"]:
            raise InvalidError("ownership goes to a user or a team")
        if kind == KIND_VE:
            self._authz.require_ve(actor, target, R.OWNER)
            ve = self._ves.find_one({"_id": target})
            if receiver["type"] == "team":
                plan = self._plans.find_one({"_id": ve["plan_id"]}) or {}
                if plan.get("personal_only"):
                    raise InvalidError("this plan is personal only")
            target_id, target_name = target, ve["name"]
        elif kind == KIND_BUCKET:
            doc = self._buckets.resolve(target)
            self._authz.require_bucket(actor, doc["_id"], R.OWNER)
            target_id, target_name = doc["_id"], doc["full_name"]
        elif kind == KIND_TEAM:
            self._teams.require(actor, target, R.OWNER)
            if receiver["type"] != "user":
                raise InvalidError("a team goes to a person")
            target_id, target_name = target, self._teams.get(target)["name"]
        else:
            raise InvalidError("kind is ve, bucket or team")
        if self._transfers.find_one({"kind": kind, "target_id": target_id, "status": PENDING}):
            raise ConflictError("a transfer is already waiting for an answer")
        doc = {"_id": f"trf-{uuid.uuid4().hex[:16]}", "kind": kind, "target_id": target_id,
               "target_name": target_name, "to": {k: receiver[k] for k in ("type", "id", "name")},
               "from": actor.user_id, "from_name": actor.username, "status": PENDING,
               "created_at": self._now_fn()}
        self._transfers.insert_one(doc)
        self._notifications.notify(self._answerers({"invitee": doc["to"]}) if receiver["type"] == "user"
                                   else [self._teams.get(receiver["id"])["owner_id"]],
                                   N.TRANSFER_REQUESTED,
                                   f"{actor.username} wants to give you {kind} {target_name}",
                                   {"transfer_id": doc["_id"]})
        return _public(doc)

    def answer_transfer(self, actor: Actor, transfer_id: str, accept: bool) -> Dict[str, Any]:
        doc = self._transfers.find_one({"_id": transfer_id, "status": PENDING})
        if doc is None:
            raise NotFoundError("no such pending transfer")
        to = doc["to"]
        allowed = to["id"] == actor.user_id if to["type"] == "user" else \
            self._teams.role_of(actor.user_id, to["id"]) == R.OWNER
        if not allowed:
            raise ForbiddenError("only the receiver (or the receiving Team Owner) answers")
        status = ACCEPTED if accept else REJECTED
        if accept:
            new_owner = {"kind": to["type"], "id": to["id"], "name": to["name"]}
            if doc["kind"] == KIND_VE:
                self._move_ve(doc["target_id"], new_owner, previous=doc["from"])
            elif doc["kind"] == KIND_BUCKET:
                self._buckets.complete_transfer(doc["target_id"], new_owner, by=actor.user_id)
            else:
                self._teams.complete_transfer(doc["target_id"], to["id"], by=actor.user_id)
        self._transfers.update_one({"_id": transfer_id}, {"$set": {"status": status, "answered_by": actor.user_id,
                                                                  "answered_at": self._now_fn()}})
        self._notifications.notify([doc["from"]], N.TRANSFER_COMPLETED,
                                   f"{to['name']} {status} {doc['kind']} {doc['target_name']}",
                                   {"transfer_id": transfer_id})
        return {**_public(doc), "status": status}

    def _move_ve(self, ve_id: str, new_owner: Dict[str, str], previous: str) -> None:
        resource = R.ve_resource(ve_id)
        if new_owner["kind"] == "user":
            self._identity.transfer(resource, new_owner["id"], "USER")
        else:
            groups = self._teams.groups(new_owner["id"])
            self._identity.transfer(resource, groups["owner"], "GROUP")
            self._identity.grant(resource, groups["admins"], "GROUP", R.XOLO_PERMISSIONS[R.ADMIN])
            self._identity.grant(resource, groups["members"], "GROUP", R.XOLO_PERMISSIONS[R.VIEWER])
        # The previous owner keeps working in it as an Admin (removable).
        self._identity.grant(resource, previous, "USER", R.XOLO_PERMISSIONS[R.ADMIN])
        kind = V.OWNER_USER if new_owner["kind"] == "user" else V.OWNER_TEAM
        self._ves.update_one({"_id": ve_id}, {"$set": {"owner": {"kind": kind, "id": new_owner["id"],
                                                                "name": new_owner["name"]}}})
        self._access.changed_resource(resource)
        self._history.append(f"ve-{ve_id}", "VirtualEnvironmentTransferred",
                             {"ve_id": ve_id, "from": previous, "to": new_owner, "at": self._now_fn()})

    def pending_transfers(self, actor: Actor, answered_days: int = 0) -> List[Dict[str, Any]]:
        """Transfers waiting for this person (or a team they own), and the ones
        they asked for; with ``answered_days``, also those answered since. A
        VE's carries its plan and monthly fee: accepting means paying it."""
        team_ids = [t["team_id"] for t in self._teams.of_user(actor.user_id) if t["owner_id"] == actor.user_id]
        status: Any = PENDING
        if answered_days:
            status = {"$in": [PENDING, ACCEPTED, REJECTED, CANCELLED]}
        query = {"$or": [{"to.id": actor.user_id}, {"to.id": {"$in": team_ids}}, {"from": actor.user_id}],
                 "status": status}
        out = []
        for doc in self._transfers.find(query).sort("created_at", -1):
            if doc["status"] != PENDING and (doc.get("answered_at") or 0) < self._now_fn() - answered_days * 86400:
                continue
            row = _public(doc)
            if doc["kind"] == KIND_VE:
                ve = self._ves.find_one({"_id": doc["target_id"]}) or {}
                plan = self._plans.find_one({"_id": ve.get("plan_id")}) or {}
                row["plan"] = {"plan_id": ve.get("plan_id"), "name": plan.get("name"),
                               "monthly_fee_cents": plan.get("monthly_fee_cents")}
            out.append(row)
        return out

    def cancel_transfer(self, actor: Actor, transfer_id: str) -> None:
        """The owner who asked changes their mind (before it's answered)."""
        doc = self._transfers.find_one({"_id": transfer_id, "status": PENDING})
        if doc is None:
            raise NotFoundError("no such pending transfer")
        if doc["from"] != actor.user_id:
            raise ForbiddenError("only who asked for the transfer cancels it")
        self._transfers.update_one({"_id": transfer_id}, {"$set": {"status": CANCELLED, "answered_by": actor.user_id,
                                                                  "answered_at": self._now_fn()}})
        to = doc["to"]
        receivers = [to["id"]] if to["type"] == "user" else [self._teams.get(to["id"])["owner_id"]]
        self._notifications.notify(receivers, N.TRANSFER_COMPLETED,
                                   f"{actor.username} cancelled giving you {doc['kind']} {doc['target_name']}",
                                   {"transfer_id": transfer_id})


def _hash(code: str) -> str:
    return hashlib.sha256(code.encode()).hexdigest()


def _public(doc: Dict[str, Any]) -> Dict[str, Any]:
    out = {k: v for k, v in doc.items() if k not in ("_id", "code_hash")}
    out["id"] = doc["_id"]
    return out
