from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from pymongo.database import Database
from pymongo.errors import DuplicateKeyError

from axo_control_plane.application.access import AccessResolver
from axo_control_plane.application.cache import StaleOkCache
from axo_control_plane.application.ports import HistoryLog, IdentityProvider, IdentityUser
from axo_control_plane.domain import roles as R
from axo_control_plane.domain.errors import (
    ConflictError,
    ForbiddenError,
    InvalidError,
    NotFoundError,
    UnauthorizedError,
)
from axo_control_plane.domain.names import check_owner_name

PLATFORM_GROUP_NAMES = {
    R.PLATFORM_ADMIN: "platform-admins",
    R.PLATFORM_USER: "platform-users",
    R.PLATFORM_GUEST: "platform-guests",
}


@dataclass(frozen=True)
class Actor:
    """Who is calling, after the session (or SDK key) was checked.

    ``key_ve_id`` / ``key_max_role`` are set when the call came with an SDK
    key: the key only works in that VE (if given), and never above that role
    (nor above the person's own current role)."""

    user_id: str
    username: str
    platform_role: Optional[str]
    password_change_required: bool = False
    key_id: Optional[str] = None
    key_ve_id: Optional[str] = None
    key_max_role: Optional[str] = None
    extra: Dict[str, Any] = field(default_factory=dict)

    @property
    def is_platform_admin(self) -> bool:
        return self.platform_role == R.PLATFORM_ADMIN

    @property
    def is_guest(self) -> bool:
        return self.platform_role == R.PLATFORM_GUEST


class Names:
    """Users and teams share ONE list of names: a name is taken by inserting
    it (unique _id), so two copies can't hand out the same name."""

    def __init__(self, db: Database) -> None:
        self._col = db["names"]

    def take(self, name: str, kind: str, owner_id: str, allow_reserved: bool = False) -> str:
        """``allow_reserved`` only for the bootstrap admin (e.g. "admin")."""
        name = (name or "").strip().lower() if allow_reserved else check_owner_name(name)
        try:
            self._col.insert_one({"_id": name, "kind": kind, "id": owner_id})
        except DuplicateKeyError:
            raise ConflictError(f"the name {name!r} is already taken", code="NAME_TAKEN")
        return name

    def set_owner(self, name: str, owner_id: str) -> None:
        self._col.update_one({"_id": name}, {"$set": {"id": owner_id}})

    def release(self, name: str) -> None:
        self._col.delete_one({"_id": name})

    def lookup(self, name: str) -> Optional[Dict[str, Any]]:
        return self._col.find_one({"_id": (name or "").strip().lower()})


class People:
    """Accounts, sessions and platform roles. Xolo holds the accounts,
    passwords, sessions and (as groups) platform roles; the CP keeps a small
    directory for lookups (username / email -> id) and the bootstrap admin's
    must-change-password flag."""

    def __init__(
        self,
        db: Database,
        identity: IdentityProvider,
        access: AccessResolver,
        names: Names,
        history: HistoryLog,
        session_cache_s: float = 60.0,
        now_fn: Callable[[], float] = time.time,
    ) -> None:
        self._users = db["users"]
        self._platform = db["platform"]
        self._identity = identity
        self._access = access
        self._names = names
        self._history = history
        self._sessions: StaleOkCache[IdentityUser] = StaleOkCache(session_cache_s, now_fn=now_fn)
        self._now_fn = now_fn
        self._load_platform_groups()

    # ── platform groups ──────────────────────────────────────────────────────
    def _load_platform_groups(self) -> None:
        doc = self._platform.find_one({"_id": "groups"}) or {}
        self._access.platform_groups.update({k: v for k, v in doc.items() if k != "_id"})

    def ensure_platform_groups(self, owner_id: str) -> Dict[str, str]:
        self._load_platform_groups()
        groups = dict(self._access.platform_groups)
        for role, group_name in PLATFORM_GROUP_NAMES.items():
            if not groups.get(role):
                groups[role] = self._identity.create_group(group_name, owner_id)
                self._platform.update_one({"_id": "groups"}, {"$set": {role: groups[role]}}, upsert=True)
        self._access.platform_groups.update(groups)
        return groups

    def set_platform_role(self, user_id: str, role: str, by: str) -> None:
        if role not in R.PLATFORM_ROLES:
            raise InvalidError(f"platform role must be one of {R.PLATFORM_ROLES}")
        groups = self._access.platform_groups
        for other, group_id in groups.items():
            if other != role and group_id:
                self._identity.remove_members(group_id, [user_id])
        self._identity.add_members(groups[role], [user_id])
        self._users.update_one({"_id": user_id}, {"$set": {"platform_role": role}})
        self._access.changed_principal(user_id)
        self._history.append(f"user-{user_id}", "PlatformRoleChanged", {"user_id": user_id, "role": role, "by": by,
                                                                        "at": self._now_fn()})

    # ── accounts ─────────────────────────────────────────────────────────────
    def signup(self, username: str, email: str, password: str, first_name: str = "", last_name: str = "",
               platform_role: str = R.PLATFORM_USER, password_change_required: bool = False) -> Dict[str, Any]:
        if len(password or "") < 8:
            raise InvalidError("the password needs at least 8 characters")
        name = self._names.take(username, "user", "")
        try:
            user = self._identity.signup(name, email, password, first_name, last_name)
        except Exception:
            self._names.release(name)
            raise
        self._names.set_owner(name, user.user_id)
        doc = {
            "_id": user.user_id, "username": name, "email": (email or "").strip().lower(),
            "first_name": first_name, "last_name": last_name, "platform_role": platform_role,
            "password_change_required": password_change_required, "created_at": self._now_fn(),
        }
        self._users.replace_one({"_id": user.user_id}, doc, upsert=True)
        group_id = self._access.platform_groups.get(platform_role)
        if group_id:
            self._identity.add_members(group_id, [user.user_id])
            self._access.changed_principal(user.user_id)
        self._history.append(f"user-{user.user_id}", "UserSignedUp",
                             {"user_id": user.user_id, "username": name, "platform_role": platform_role,
                              "at": self._now_fn()})
        return self.public(doc)

    def login(self, username: str, password: str) -> Dict[str, Any]:
        session = self._identity.login((username or "").strip().lower(), password)
        self._remember_user(session.user)
        self._sessions.put(_session_key(session.access_token, session.temporal_secret), session.user)
        doc = self._users.find_one({"_id": session.user.user_id}) or {}
        return {
            "access_token": session.access_token,
            "temporal_secret": session.temporal_secret,
            "user": self.public(doc),
        }

    def refresh(self, access_token: str, temporal_secret: str) -> Dict[str, Any]:
        session = self._identity.refresh(access_token, temporal_secret)
        self._sessions.drop(lambda k: k == _session_key(access_token, temporal_secret))
        self._sessions.put(_session_key(session.access_token, session.temporal_secret), session.user)
        return {"access_token": session.access_token, "temporal_secret": session.temporal_secret}

    def logout(self, access_token: str, temporal_secret: str) -> None:
        user = self.verify(access_token, temporal_secret)
        self._sessions.drop(lambda k: k == _session_key(access_token, temporal_secret))
        self._identity.logout(access_token, temporal_secret, user.username)

    def change_password(self, actor: Actor, access_token: str, temporal_secret: str, new_password: str) -> None:
        if len(new_password or "") < 8:
            raise InvalidError("the password needs at least 8 characters")
        self._identity.change_password(actor.username, access_token, temporal_secret, new_password)
        # Xolo ends the session with the password change: log in again.
        self._sessions.drop(lambda k: k == _session_key(access_token, temporal_secret))
        self._users.update_one({"_id": actor.user_id}, {"$set": {"password_change_required": False}})
        self._history.append(f"user-{actor.user_id}", "PasswordChanged", {"user_id": actor.user_id,
                                                                          "at": self._now_fn()})

    def verify(self, access_token: str, temporal_secret: str) -> IdentityUser:
        """The session's user. Xolo checks the session still exists (a
        logged-out token stops working); answers are cached ~1 minute."""
        if not access_token or not temporal_secret:
            raise UnauthorizedError("expected Authorization: Bearer <token> and a Temporal-Secret-Key header")
        user = self._sessions.get(_session_key(access_token, temporal_secret),
                                  lambda: self._identity.introspect(access_token, temporal_secret))
        if user.disabled:
            raise UnauthorizedError("this account is disabled")
        return user

    def actor_for(self, user: IdentityUser) -> Actor:
        doc = self._users.find_one({"_id": user.user_id})
        if doc is None:
            doc = self._remember_user(user)
        return Actor(
            user_id=user.user_id, username=user.username,
            platform_role=self._access.platform_role(user.user_id) or doc.get("platform_role"),
            password_change_required=bool(doc.get("password_change_required")),
        )

    def _remember_user(self, user: IdentityUser) -> Dict[str, Any]:
        doc = self._users.find_one({"_id": user.user_id})
        if doc is None:
            doc = {"_id": user.user_id, "username": user.username, "email": (user.email or "").lower(),
                   "first_name": user.first_name, "last_name": user.last_name,
                   "platform_role": R.PLATFORM_USER, "password_change_required": False,
                   "created_at": self._now_fn()}
            self._users.insert_one(doc)
        return doc

    # ── lookups ──────────────────────────────────────────────────────────────
    def get(self, user_id: str) -> Dict[str, Any]:
        doc = self._users.find_one({"_id": user_id})
        if doc is None:
            raise NotFoundError("no such user")
        return self.public(doc)

    def find(self, username_or_email: str) -> Optional[Dict[str, Any]]:
        key = (username_or_email or "").strip().lower()
        doc = self._users.find_one({"$or": [{"username": key}, {"email": key}]})
        return self.public(doc) if doc else None

    def list(self, limit: int = 200) -> List[Dict[str, Any]]:
        return [self.public(d) for d in self._users.find().sort("username", 1).limit(limit)]

    @staticmethod
    def public(doc: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "user_id": doc.get("_id"), "username": doc.get("username"), "email": doc.get("email"),
            "first_name": doc.get("first_name", ""), "last_name": doc.get("last_name", ""),
            "platform_role": doc.get("platform_role"),
            "password_change_required": bool(doc.get("password_change_required")),
        }

    # ── bootstrap admin ──────────────────────────────────────────────────────
    def bootstrap_admin(self, username: str, password: str, email: str) -> str:
        """At deploy time: create the Platform Admin if missing (must change
        the password at first login) and the platform role groups. Later
        deploys change nothing."""
        doc = self._users.find_one({"username": username})
        if doc is not None:
            self.ensure_platform_groups(doc["_id"])
            return "exists"
        user = None
        try:
            user = self._identity.signup(username, email, password)
        except ConflictError:
            pass
        if user is None:
            # Already in Xolo (e.g. the CP database was reset): log in to learn
            # its id. If the password was changed since, this fails loudly.
            user = self._identity.login(username, password).user
        if self._names.lookup(username) is None:
            self._names.take(username, "user", user.user_id, allow_reserved=True)
        self._users.replace_one({"_id": user.user_id}, {
            "_id": user.user_id, "username": username, "email": email, "first_name": "", "last_name": "",
            "platform_role": R.PLATFORM_ADMIN, "password_change_required": True, "created_at": self._now_fn(),
        }, upsert=True)
        groups = self.ensure_platform_groups(user.user_id)
        self._identity.add_members(groups[R.PLATFORM_ADMIN], [user.user_id])
        self._access.changed_principal(user.user_id)
        self._history.append(f"user-{user.user_id}", "BootstrapAdminCreated", {"user_id": user.user_id,
                                                                               "at": self._now_fn()})
        return "created"

    def require(self, actor: Actor, platform_role: str) -> None:
        if R.rank(actor.platform_role, R.PLATFORM_ROLES) < R.rank(platform_role, R.PLATFORM_ROLES):
            raise ForbiddenError(f"needs the platform role {platform_role}")


def _session_key(access_token: str, temporal_secret: str) -> str:
    return hashlib.sha256(f"{access_token}\x00{temporal_secret}".encode()).hexdigest()
