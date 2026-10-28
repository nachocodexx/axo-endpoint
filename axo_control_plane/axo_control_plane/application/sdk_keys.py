from __future__ import annotations

import hashlib
import hmac
import secrets
import time
import uuid
from typing import Any, Callable, Dict, List, Optional

from pymongo.database import Database

from axo_control_plane.application.authz import Authorizer
from axo_control_plane.application.people import Actor, People
from axo_control_plane.application.ports import HistoryLog
from axo_control_plane.domain import roles as R
from axo_control_plane.domain.errors import ForbiddenError, InvalidError, NotFoundError, UnauthorizedError

KEY_PREFIX = "axo_"


class SdkKeys:
    """Keys for programs (the SDK). A key belongs to one person and, if
    given, one VE; it has a maximum role (never above the person's own) and
    an optional expiry. Only a keyed hash is stored; the key is shown once.
    Every use takes the lower of the key's role and the person's role at
    that moment, so lowering or removing someone's access also limits keys.

    Format: ``axo_<key id>_<secret>`` (the id finds the record; the secret is
    compared in constant time)."""

    def __init__(self, db: Database, people: People, authz: Authorizer, history: HistoryLog, pepper: str,
                 now_fn: Callable[[], float] = time.time) -> None:
        if not pepper:
            raise ValueError("an SDK key pepper (AXO_CP_SDK_KEY_PEPPER) is required")
        self._col = db["sdk_keys"]
        self._users = db["users"]
        self._people = people
        self._authz = authz
        self._history = history
        self._pepper = pepper.encode()
        self._now_fn = now_fn

    def _hash(self, secret: str) -> str:
        return hmac.new(self._pepper, secret.encode(), hashlib.sha256).hexdigest()

    def create(self, actor: Actor, name: str, ve_id: Optional[str], max_role: str,
               expires_in_days: Optional[float] = None) -> Dict[str, Any]:
        if actor.key_id is not None:
            raise ForbiddenError("an API key can't create keys")
        if actor.is_guest:
            raise ForbiddenError("guest accounts can't create keys")
        R.check_ve_role(max_role)
        if ve_id:
            mine = self._authz.require_ve(actor, ve_id, R.VIEWER)
            if not R.at_least(mine, max_role):
                raise ForbiddenError(f"a key can't have more than your own role ({mine})")
        if not (name or "").strip() or len(name) > 80:
            raise InvalidError("a key needs a name (up to 80 characters)")
        key_id = uuid.uuid4().hex[:12]
        secret = secrets.token_urlsafe(32)
        now = self._now_fn()
        doc = {"_id": key_id, "hash": self._hash(secret), "user_id": actor.user_id, "name": name.strip(),
               "ve_id": ve_id or None, "max_role": max_role, "created_at": now,
               "expires_at": now + expires_in_days * 86400 if expires_in_days else None,
               "revoked": False, "last_used_at": None}
        self._col.insert_one(doc)
        self._history.append(f"user-{actor.user_id}", "SdkKeyCreated", {"key_id": key_id, "ve_id": ve_id,
                                                                        "max_role": max_role, "at": now})
        return {**_public(doc), "key": f"{KEY_PREFIX}{key_id}_{secret}"}

    def list(self, actor: Actor) -> List[Dict[str, Any]]:
        docs = self._col.find({"user_id": actor.user_id, "revoked": False}).sort("created_at", -1)
        return [_public(d) for d in docs]

    def revoke(self, actor: Actor, key_id: str) -> None:
        doc = self._col.find_one({"_id": key_id})
        if doc is None or (doc["user_id"] != actor.user_id and not actor.is_platform_admin):
            raise NotFoundError("no such key")
        self._col.update_one({"_id": key_id}, {"$set": {"revoked": True, "revoked_at": self._now_fn()}})
        self._history.append(f"user-{doc['user_id']}", "SdkKeyRevoked", {"key_id": key_id, "by": actor.user_id,
                                                                         "at": self._now_fn()})

    def authenticate(self, presented: str) -> Actor:
        """The Actor a key acts as, or UnauthorizedError (same message for
        every failure, so a guesser learns nothing)."""
        invalid = UnauthorizedError("invalid API key")
        if not presented or not presented.startswith(KEY_PREFIX):
            raise invalid
        key_id, sep, secret = presented[len(KEY_PREFIX):].partition("_")
        if not sep or not key_id or not secret:
            raise invalid
        doc = self._col.find_one({"_id": key_id})
        if doc is None or not hmac.compare_digest(doc["hash"], self._hash(secret)):
            raise invalid
        now = self._now_fn()
        if doc.get("revoked") or (doc.get("expires_at") and now >= doc["expires_at"]):
            raise invalid
        user = self._users.find_one({"_id": doc["user_id"]})
        if user is None:
            raise invalid
        if not doc.get("last_used_at") or now - doc["last_used_at"] > 60:
            self._col.update_one({"_id": key_id}, {"$set": {"last_used_at": now}})
        return Actor(user_id=doc["user_id"], username=user["username"],
                     platform_role=user.get("platform_role"), key_id=key_id,
                     key_ve_id=doc.get("ve_id"), key_max_role=doc["max_role"], extra={"key_name": doc.get("name")})


def _public(doc: Dict[str, Any]) -> Dict[str, Any]:
    return {"key_id": doc["_id"], "name": doc["name"], "ve_id": doc.get("ve_id"), "max_role": doc["max_role"],
            "created_at": doc["created_at"], "expires_at": doc.get("expires_at"),
            "last_used_at": doc.get("last_used_at")}
