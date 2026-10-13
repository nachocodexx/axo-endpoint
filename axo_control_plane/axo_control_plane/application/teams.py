from __future__ import annotations

import time
import uuid
from typing import Any, Callable, Dict, List, Optional

from pymongo.database import Database

from axo_control_plane.application.access import AccessResolver
from axo_control_plane.application.people import Actor, Names
from axo_control_plane.application.ports import HistoryLog, IdentityProvider
from axo_control_plane.domain import roles as R
from axo_control_plane.domain.errors import ConflictError, ForbiddenError, InvalidError, NotFoundError

# Three Xolo groups per team, so Xolo's "union of grants" gives each team
# role exactly its rights on team-owned VEs / buckets:
#   owner   -> only the Team Owner; it OWNS the team's VEs and buckets
#   admins  -> Team Admins (and the Owner); granted VE Admin on team VEs
#   members -> everyone in the team; granted read on team VEs, and whatever
#              role a VE/bucket is shared with "the team"
GROUP_KINDS = ("owner", "admins", "members")


class Teams:
    def __init__(self, db: Database, identity: IdentityProvider, access: AccessResolver, names: Names,
                 history: HistoryLog, now_fn: Callable[[], float] = time.time) -> None:
        self._col = db["teams"]
        self._ves = db["virtual_environments"]
        self._buckets = db["buckets"]
        self._identity = identity
        self._access = access
        self._names = names
        self._history = history
        self._now_fn = now_fn

    # ── reading ──────────────────────────────────────────────────────────────
    def _doc(self, team_id: str) -> Dict[str, Any]:
        doc = self._col.find_one({"_id": team_id, "deleted": {"$ne": True}})
        if doc is None:
            raise NotFoundError("no such team")
        return doc

    def get(self, team_id: str) -> Dict[str, Any]:
        return _public(self._doc(team_id))

    def by_name(self, name: str) -> Optional[Dict[str, Any]]:
        doc = self._col.find_one({"name": name, "deleted": {"$ne": True}})
        return _public(doc) if doc else None

    def groups(self, team_id: str) -> Dict[str, str]:
        return dict(self._doc(team_id)["groups"])

    def role_of(self, user_id: str, team_id: str) -> Optional[str]:
        """From Xolo group membership (cached), not from the local list."""
        groups = self._doc(team_id)["groups"]
        mine = set(self._access.groups_of(user_id))
        if groups["owner"] in mine:
            return R.OWNER
        if groups["admins"] in mine:
            return R.ADMIN
        if groups["members"] in mine:
            return R.MEMBER
        return None

    def require(self, actor: Actor, team_id: str, minimum: str) -> str:
        role = self.role_of(actor.user_id, team_id)
        if not R.at_least(role, minimum, R.TEAM_ROLES):
            raise ForbiddenError(f"needs the team role {minimum}")
        return role  # type: ignore[return-value]

    def of_user(self, user_id: str) -> List[Dict[str, Any]]:
        return [_public(d) for d in self._col.find({f"members.{user_id}": {"$exists": True},
                                                     "deleted": {"$ne": True}}).sort("name", 1)]

    # ── changes ──────────────────────────────────────────────────────────────
    def create(self, actor: Actor, name: str, description: str = "") -> Dict[str, Any]:
        if actor.is_guest:
            raise ForbiddenError("guest accounts can't create teams")
        team_id = f"team-{uuid.uuid4().hex[:12]}"
        name = self._names.take(name, "team", team_id)
        created: Dict[str, str] = {}
        try:
            for kind in GROUP_KINDS:
                created[kind] = self._identity.create_group(f"team:{team_id}:{kind}", actor.user_id)
            for kind in GROUP_KINDS:
                self._identity.add_members(created[kind], [actor.user_id])
        except Exception:
            for group_id in created.values():
                _quietly(self._identity.delete_group, group_id)
            self._names.release(name)
            raise
        doc = {"_id": team_id, "name": name, "description": description, "owner_id": actor.user_id,
               "groups": created, "members": {actor.user_id: R.OWNER},
               "limits": {"per_admin_cents": None, "team_cents": None},
               "created_at": self._now_fn(), "created_by": actor.user_id}
        self._col.insert_one(doc)
        self._access.changed_principal(actor.user_id)
        self._record(team_id, "TeamCreated", actor, name=name)
        return _public(doc)

    def add_member(self, team_id: str, user_id: str, role: str, by: str) -> None:
        """Called once an invitation is accepted."""
        if role not in (R.MEMBER, R.ADMIN):
            raise InvalidError("a new member is a member or an admin")
        groups = self._doc(team_id)["groups"]
        self._identity.add_members(groups["members"], [user_id])
        if role == R.ADMIN:
            self._identity.add_members(groups["admins"], [user_id])
        self._col.update_one({"_id": team_id}, {"$set": {f"members.{user_id}": role}})
        self._access.changed_principal(user_id)
        self._history.append(f"team-{team_id}", "TeamMemberAdded",
                             {"team_id": team_id, "user_id": user_id, "role": role, "by": by, "at": self._now_fn()})

    def change_role(self, actor: Actor, team_id: str, user_id: str, role: str) -> None:
        actor_role = self.require(actor, team_id, R.ADMIN)
        if role not in (R.MEMBER, R.ADMIN):
            raise InvalidError("the role is member or admin (ownership changes by transfer)")
        current = self.role_of(user_id, team_id)
        if current is None:
            raise NotFoundError("that person is not in the team")
        if current == R.OWNER:
            raise ForbiddenError("the Team Owner's role changes only by transfer")
        if current == R.ADMIN and actor_role != R.OWNER and user_id != actor.user_id:
            raise ForbiddenError("only the Team Owner changes another admin's role")
        groups = self._doc(team_id)["groups"]
        if role == R.ADMIN:
            self._identity.add_members(groups["admins"], [user_id])
        else:
            self._identity.remove_members(groups["admins"], [user_id])
        self._col.update_one({"_id": team_id}, {"$set": {f"members.{user_id}": role}})
        self._access.changed_principal(user_id)
        self._record(team_id, "TeamRoleChanged", actor, user_id=user_id, role=role)

    def remove_member(self, actor: Actor, team_id: str, user_id: str) -> None:
        """Leaving a team removes every access it gave (group membership)."""
        target = self.role_of(user_id, team_id)
        if target is None:
            raise NotFoundError("that person is not in the team")
        if target == R.OWNER:
            raise ForbiddenError("the Team Owner can't leave; transfer the team or delete it")
        if user_id != actor.user_id:
            actor_role = self.require(actor, team_id, R.ADMIN)
            if target == R.ADMIN and actor_role != R.OWNER:
                raise ForbiddenError("only the Team Owner removes an admin")
        groups = self._doc(team_id)["groups"]
        for kind in ("admins", "members"):
            self._identity.remove_members(groups[kind], [user_id])
        self._col.update_one({"_id": team_id}, {"$unset": {f"members.{user_id}": ""}})
        self._access.changed_principal(user_id)
        self._record(team_id, "TeamMemberRemoved", actor, user_id=user_id)

    def complete_transfer(self, team_id: str, new_owner_id: str, by: str) -> None:
        """Both sides already agreed (see Transfers). The old owner stays an admin."""
        doc = self._doc(team_id)
        groups, old_owner = doc["groups"], doc["owner_id"]
        for kind in GROUP_KINDS:
            self._identity.add_members(groups[kind], [new_owner_id])
            self._identity.transfer_group(groups[kind], new_owner_id)
        self._identity.remove_members(groups["owner"], [old_owner])
        self._col.update_one({"_id": team_id}, {"$set": {"owner_id": new_owner_id,
                                                        f"members.{new_owner_id}": R.OWNER,
                                                        f"members.{old_owner}": R.ADMIN}})
        self._names.set_owner(doc["name"], team_id)
        for user_id in (old_owner, new_owner_id):
            self._access.changed_principal(user_id)
        self._history.append(f"team-{team_id}", "TeamTransferred",
                             {"team_id": team_id, "from": old_owner, "to": new_owner_id, "by": by,
                              "at": self._now_fn()})

    def update(self, actor: Actor, team_id: str, description: Optional[str] = None,
               per_admin_cents: Optional[int] = -1, team_cents: Optional[int] = -1) -> Dict[str, Any]:
        """Description: Owner/Admin. Spending limits (cents per month; None = no
        limit; -1 = leave as is): Owner only."""
        changes: Dict[str, Any] = {}
        if description is not None:
            self.require(actor, team_id, R.ADMIN)
            changes["description"] = description
        if per_admin_cents != -1 or team_cents != -1:
            self.require(actor, team_id, R.OWNER)
            for key, value in (("per_admin_cents", per_admin_cents), ("team_cents", team_cents)):
                if value != -1:
                    if value is not None and value < 0:
                        raise InvalidError("a spending limit can't be negative")
                    changes[f"limits.{key}"] = value
        if changes:
            self._col.update_one({"_id": team_id}, {"$set": changes})
            self._record(team_id, "TeamUpdated", actor, changes=changes)
        return self.get(team_id)

    def delete(self, actor: Actor, team_id: str) -> None:
        self.require(actor, team_id, R.OWNER)
        doc = self._doc(team_id)
        if self._ves.count_documents({"owner.kind": "team", "owner.id": team_id, "desired": {"$ne": "deleted"}}):
            raise ConflictError("the team still owns VEs; delete or transfer them first")
        if self._buckets.count_documents({"owner.kind": "team", "owner.id": team_id, "deleted": {"$ne": True}}):
            raise ConflictError("the team still owns buckets; delete or transfer them first")
        members = list(doc.get("members", {}))
        for group_id in doc["groups"].values():
            _quietly(self._identity.delete_group, group_id)
        self._col.update_one({"_id": team_id}, {"$set": {"deleted": True, "deleted_at": self._now_fn()}})
        self._names.release(doc["name"])
        for user_id in members:
            self._access.changed_principal(user_id)
        self._record(team_id, "TeamDeleted", actor)

    def _record(self, team_id: str, event_type: str, actor: Actor, **data: Any) -> None:
        self._history.append(f"team-{team_id}", event_type,
                             {"team_id": team_id, "by": actor.user_id, "at": self._now_fn(), **data})


def _public(doc: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "team_id": doc["_id"], "name": doc["name"], "description": doc.get("description", ""),
        "owner_id": doc["owner_id"], "members": [{"user_id": u, "role": r} for u, r in doc.get("members", {}).items()],
        "limits": doc.get("limits", {}), "created_at": doc.get("created_at"),
    }


def _quietly(fn, *args) -> None:
    try:
        fn(*args)
    except Exception:
        pass
