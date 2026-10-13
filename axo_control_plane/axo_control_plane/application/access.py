from __future__ import annotations

import threading
import time
from collections import deque
from typing import Callable, Deque, Dict, List, Optional, Set, Tuple

from pymongo.database import Database

from axo_control_plane.application.cache import StaleOkCache
from axo_control_plane.application.ports import Grant, IdentityProvider
from axo_control_plane.domain import roles as R


class Invalidations:
    """Tells every Control Plane copy to forget cached roles after a change
    made through any copy: a small Mongo collection each copy polls. Changes
    made directly in Xolo (outside the CP) show up when the cache expires."""

    def __init__(self, db: Database, now_fn: Callable[[], float] = time.time) -> None:
        self._col = db["cache_invalidations"]
        self._now_fn = now_fn
        self._listeners: List[Callable[[str, str], None]] = []
        self._last_at = now_fn()
        self._seen: Deque[str] = deque(maxlen=5000)
        self._seen_set: Set[str] = set()

    def subscribe(self, listener: Callable[[str, str], None]) -> None:
        self._listeners.append(listener)

    def publish(self, kind: str, key: str) -> None:
        """kind: "principal" (a user's or group's groups/grants changed) or
        "resource" (a VE's / bucket's grants changed)."""
        result = self._col.insert_one({"at": self._now_fn(), "kind": kind, "key": key})
        self._remember(str(result.inserted_id))
        self._notify(kind, key)

    def poll(self) -> int:
        since = self._last_at - 2.0          # small overlap for clock skew
        applied = 0
        for doc in self._col.find({"at": {"$gte": since}}).sort("at", 1):
            doc_id = str(doc["_id"])
            self._last_at = max(self._last_at, doc["at"])
            if doc_id in self._seen_set:
                continue
            self._remember(doc_id)
            self._notify(doc["kind"], doc["key"])
            applied += 1
        self._col.delete_many({"at": {"$lt": self._now_fn() - 3600}})
        return applied

    def run_forever(self, stop_event: threading.Event, interval_s: float = 1.0) -> None:
        while not stop_event.wait(interval_s):
            try:
                self.poll()
            except Exception:
                pass

    def _remember(self, doc_id: str) -> None:
        if len(self._seen) == self._seen.maxlen:
            self._seen_set.discard(self._seen[0])
        self._seen.append(doc_id)
        self._seen_set.add(doc_id)

    def _notify(self, kind: str, key: str) -> None:
        for listener in self._listeners:
            listener(kind, key)


class AccessResolver:
    """Who may do what, from Xolo (the source of truth) through a cache.

    A person's role on a VE / bucket = the strongest of:
    - Owner, if the resource's owner is the person or a group they're in
      (a team-owned resource is owned by the team's ``owner`` group, which
      holds only the Team Owner);
    - the role matching the union of permissions granted to the person and
      to their groups (roles are stored as cumulative permission sets).
    """

    def __init__(self, identity: IdentityProvider, invalidations: Invalidations, ttl_s: float = 300.0,
                 platform_groups: Optional[Dict[str, str]] = None,
                 now_fn: Callable[[], float] = time.monotonic) -> None:
        self._identity = identity
        self._grants: StaleOkCache[List[Grant]] = StaleOkCache(ttl_s, now_fn=now_fn)
        self._groups: StaleOkCache[Tuple[str, ...]] = StaleOkCache(ttl_s, now_fn=now_fn)
        self._resources: StaleOkCache[Tuple[str, ...]] = StaleOkCache(ttl_s, now_fn=now_fn)
        # role name -> Xolo group id (platform-admins / -users / -guests)
        self.platform_groups: Dict[str, str] = dict(platform_groups or {})
        invalidations.subscribe(self._on_invalidation)
        self._invalidations = invalidations

    # ── lookups ──────────────────────────────────────────────────────────────
    def groups_of(self, user_id: str) -> Tuple[str, ...]:
        return self._groups.get(user_id, lambda: tuple(self._identity.groups_of(user_id)))

    def grants(self, resource: str) -> List[Grant]:
        return self._grants.get(resource, lambda: list(self._identity.grants(resource)))

    def visible_resources(self, user_id: str) -> Tuple[str, ...]:
        return self._resources.get(user_id, lambda: tuple(self._identity.resources_of(user_id)))

    def _effective(self, user_id: str, resource: str) -> Tuple[Set[str], bool]:
        principals = {user_id, *self.groups_of(user_id)}
        perms: Set[str] = set()
        is_owner = False
        for grant in self.grants(resource):
            if grant.principal_id in principals:
                perms |= set(grant.permissions)
                is_owner = is_owner or grant.is_owner
        return perms, is_owner

    def ve_role(self, user_id: str, ve_id: str) -> Optional[str]:
        perms, is_owner = self._effective(user_id, R.ve_resource(ve_id))
        return R.ve_role_from_permissions(perms, is_owner)

    def bucket_role(self, user_id: str, bucket_id: str) -> Optional[str]:
        perms, is_owner = self._effective(user_id, R.bucket_resource(bucket_id))
        return R.bucket_role_from_permissions(perms, is_owner)

    def platform_role(self, user_id: str) -> Optional[str]:
        groups = set(self.groups_of(user_id))
        for role in (R.PLATFORM_ADMIN, R.PLATFORM_USER, R.PLATFORM_GUEST):
            group_id = self.platform_groups.get(role)
            if group_id and group_id in groups:
                return role
        return None

    def owner_principal(self, resource: str) -> Optional[Grant]:
        return next((g for g in self.grants(resource) if g.is_owner), None)

    # ── after a change made through this copy ───────────────────────────────
    def changed_principal(self, principal_id: str) -> None:
        self._invalidations.publish("principal", principal_id)

    def changed_resource(self, resource: str) -> None:
        self._invalidations.publish("resource", resource)

    def _on_invalidation(self, kind: str, key: str) -> None:
        if kind == "principal":
            self._groups.drop(lambda k: k == key)
            self._resources.drop(lambda k: k == key)
        elif kind == "resource":
            self._grants.drop(lambda k: k == key)
            self._resources.clear()           # who sees it may have changed
        else:
            self._groups.clear()
            self._grants.clear()
            self._resources.clear()
