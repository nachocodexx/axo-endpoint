from __future__ import annotations

import time
import uuid
from typing import Any, Callable, Dict, List, Optional

from pymongo.database import Database
from pymongo.errors import DuplicateKeyError

from axo_control_plane.application.access import AccessResolver
from axo_control_plane.application.authz import Authorizer
from axo_control_plane.application.people import Actor, Names
from axo_control_plane.application.ports import HistoryLog, IdentityProvider, StorageGateway
from axo_control_plane.application.teams import Teams
from axo_control_plane.domain import roles as R
from axo_control_plane.domain.errors import ConflictError, ForbiddenError, InvalidError, NotFoundError
from axo_control_plane.domain.names import (
    PLATFORM_OWNER,
    check_bucket_name,
    full_bucket_name,
    split_bucket_name,
    ve_of_objects_bucket,
    ve_of_results_bucket,
)

NAME = "name"
ALIAS = "alias"
# The principal the Control Plane itself uses towards axo_storage.
CP_PRINCIPAL = "control-plane"


class Buckets:
    """Buckets as people see them: ``owner/bucket`` names (owner = a user or
    team name), extra names (aliases), size limits, sharing and deletion.

    In axo_storage every bucket is stored under a fixed key (``bkt-<id>``);
    names and aliases are only ways to reach it, so renaming / transferring
    never moves data: the CP resolves a name to the key on every access check.
    """

    def __init__(self, db: Database, storage: StorageGateway, identity: IdentityProvider, access: AccessResolver,
                 authz: Authorizer, names: Names, teams: Teams, history: HistoryLog,
                 default_quota_bytes: int, max_quota_bytes: int, grace_s: float = 30 * 86400.0,
                 now_fn: Callable[[], float] = time.time) -> None:
        self._col = db["buckets"]
        self._names_col = db["bucket_names"]
        self._storage = storage
        self._identity = identity
        self._access = access
        self._authz = authz
        self._names = names
        self._teams = teams
        self._history = history
        self._default_quota = default_quota_bytes
        self._max_quota = max_quota_bytes
        self._grace_s = grace_s
        self._now_fn = now_fn

    # ── names ────────────────────────────────────────────────────────────────
    def by_key(self, bucket_id: str) -> Optional[Dict[str, Any]]:
        return self._col.find_one({"_id": bucket_id, "deleted": {"$ne": True}})

    def resolve(self, full_name: str) -> Dict[str, Any]:
        """A name or alias -> the bucket (NotFound for unknown / deleted)."""
        owner, name = split_bucket_name(full_name)
        entry = self._names_col.find_one({"_id": f"{owner}/{name}"})
        if entry is None:
            raise NotFoundError(f"no bucket {owner}/{name}")
        doc = self._col.find_one({"_id": entry["bucket_id"], "deleted": {"$ne": True}})
        if doc is None:
            raise NotFoundError(f"no bucket {owner}/{name}")
        return doc

    def _take_name(self, full_name: str, bucket_id: str, kind: str) -> None:
        try:
            self._names_col.insert_one({"_id": full_name, "bucket_id": bucket_id, "kind": kind,
                                        "created_at": self._now_fn()})
        except DuplicateKeyError:
            raise ConflictError(f"the name {full_name} is already used", code="BUCKET_NAME_TAKEN")

    def _owner_for(self, actor: Actor, owner_name: Optional[str]) -> Dict[str, str]:
        """``owner_name`` None / the actor's name = the actor; a team name =
        that team (needs Team Owner / Admin)."""
        if owner_name in (None, "", actor.username):
            return {"kind": "user", "id": actor.user_id, "name": actor.username}
        entry = self._names.lookup(owner_name)
        if entry is None or entry["kind"] != "team":
            raise ForbiddenError(f"you can only use your own name or a team you manage, not {owner_name!r}")
        self._teams.require(actor, entry["id"], R.ADMIN)
        return {"kind": "team", "id": entry["id"], "name": owner_name}

    # ── create / read ────────────────────────────────────────────────────────
    def create(self, actor: Actor, name: str, owner_name: Optional[str] = None,
               quota_bytes: Optional[int] = None) -> Dict[str, Any]:
        if actor.is_guest or (actor.key_id is not None and not R.at_least(actor.key_max_role, R.ADMIN)):
            raise ForbiddenError("guests and lower API keys can't create buckets")
        name = check_bucket_name(name)
        owner = self._owner_for(actor, owner_name)
        quota = self._check_quota(quota_bytes if quota_bytes is not None else self._default_quota)
        bucket_id = f"bkt-{uuid.uuid4().hex[:16]}"
        full_name = full_bucket_name(owner["name"], name)
        self._take_name(full_name, bucket_id, NAME)
        resource = R.bucket_resource(bucket_id)
        try:
            self._storage.create_bucket(bucket_id, actor.user_id, quota)
            self._claim(resource, owner)
        except Exception:
            self._names_col.delete_one({"_id": full_name})
            raise
        doc = {"_id": bucket_id, "name": name, "full_name": full_name, "owner": owner, "quota_bytes": quota,
               "created_by": actor.user_id, "created_at": self._now_fn(), "deleted": False}
        self._col.insert_one(doc)
        self._access.changed_resource(resource)
        self._record(bucket_id, "BucketCreated", actor, full_name=full_name, owner=owner, quota_bytes=quota)
        return self.public(doc)

    def _claim(self, resource: str, owner: Dict[str, str]) -> List[str]:
        """Makes ``owner`` the resource's owner in Xolo (a team: its owner
        group, with Writer for its admins and Reader for its members).
        Returns the principals given access this way."""
        if owner["kind"] == "user":
            self._identity.claim(resource, owner["id"], "USER")
            return [owner["id"]]
        groups = self._teams.groups(owner["id"])
        self._identity.claim(resource, groups["owner"], "GROUP")
        self._identity.grant(resource, groups["admins"], "GROUP", R.XOLO_BUCKET_PERMISSIONS[R.WRITER])
        self._identity.grant(resource, groups["members"], "GROUP", R.XOLO_BUCKET_PERMISSIONS[R.READER])
        return list(groups.values())

    def require(self, actor: Actor, full_name: str, minimum: str) -> Dict[str, Any]:
        """The bucket, if the caller has at least ``minimum`` on it."""
        doc = self.resolve(full_name)
        self._authz.require_bucket(actor, doc["_id"], minimum)
        return doc

    def get(self, actor: Actor, full_name: str) -> Dict[str, Any]:
        doc = self.resolve(full_name)
        role = self._authz.require_bucket(actor, doc["_id"], R.READER)
        return {**self.public(doc), **self._stats([doc["_id"]]).get(doc["_id"], {}), "my_role": role,
                "relation": self._relation(actor, doc)}

    def list_for(self, actor: Actor) -> List[Dict[str, Any]]:
        """Every bucket the person can reach, with its figures (one storage
        call for all) and how they reach it: ``mine`` (they own it), ``team``
        (one of their teams does) or ``shared`` (shared with them).
        Platform buckets (``axo/...``) are never listed: people reach their
        contents through the pages that use them (job results, code)."""
        ids = [r.split(":", 1)[1] for r in self._access.visible_resources(actor.user_id) if r.startswith("bucket:")]
        docs = [d for d in self._col.find({"_id": {"$in": ids}, "deleted": {"$ne": True}}).sort("full_name", 1)
                if d["owner"].get("name") != PLATFORM_OWNER]
        stats = self._stats([d["_id"] for d in docs])
        return [{**self.public(doc), **stats.get(doc["_id"], {}), "my_role": self._authz.bucket_role(actor, doc["_id"]),
                 "relation": self._relation(actor, doc)} for doc in docs]

    def _stats(self, keys: List[str]) -> Dict[str, Dict[str, Any]]:
        """Used bytes, files and last change; nothing (the page shows "—")
        when storage doesn't answer -- the list itself still loads."""
        if not keys:
            return {}
        try:
            return self._storage.bucket_stats(keys)
        except Exception:
            return {}

    def _relation(self, actor: Actor, doc: Dict[str, Any]) -> str:
        owner = doc["owner"]
        if owner["kind"] == "user":
            return "mine" if owner["id"] == actor.user_id else "shared"
        return "team" if self._teams.role_of(actor.user_id, owner["id"]) is not None else "shared"

    def _owns(self, actor: Actor, doc: Dict[str, Any]) -> bool:
        """Owner of a deleted bucket (no Xolo resource any more): the person
        it belonged to, or the Team Owner of the team it belonged to."""
        owner = doc["owner"]
        if owner["kind"] == "user":
            return owner["id"] == actor.user_id
        return self._teams.role_of(actor.user_id, owner["id"]) == R.OWNER

    def list_deleted(self, actor: Actor) -> List[Dict[str, Any]]:
        """Deleted buckets the person owns and can still restore."""
        out = []
        for doc in self._col.find({"deleted": True, "purged": {"$ne": True}}).sort("deleted_at", -1):
            if self._owns(actor, doc):
                out.append({**self.public(doc), "deleted_at": doc["deleted_at"], "purge_after": doc["purge_after"],
                            "deleted_by": doc.get("deleted_by"), "my_role": R.OWNER,
                            "relation": "mine" if doc["owner"]["kind"] == "user" else "team"})
        return out

    def _deleted(self, actor: Actor, bucket_id: str) -> Dict[str, Any]:
        doc = self._col.find_one({"_id": bucket_id, "deleted": True, "purged": {"$ne": True}})
        if doc is None or not self._owns(actor, doc):
            raise NotFoundError("no deleted bucket to restore with that id")
        return doc

    def restore(self, actor: Actor, bucket_id: str) -> Dict[str, Any]:
        """Back as it was: its files (never removed), names, size limit and
        everyone's access (saved when it was deleted)."""
        doc = self._deleted(actor, bucket_id)
        resource = R.bucket_resource(bucket_id)
        given = set(self._claim(resource, doc["owner"]))
        for grant in doc.get("saved_access") or []:
            if grant["principal_id"] not in given:
                self._identity.grant(resource, grant["principal_id"], grant["principal_type"],
                                     frozenset(grant["permissions"]))
        self._col.update_one({"_id": bucket_id}, {"$set": {"deleted": False},
                                                   "$unset": {"deleted_at": "", "deleted_by": "", "purge_after": "",
                                                              "saved_access": ""}})
        self._access.changed_resource(resource)
        self._record(bucket_id, "BucketRestored", actor, full_name=doc["full_name"])
        return self.public(self._col.find_one({"_id": bucket_id}))

    def delete_permanently(self, actor: Actor, bucket_id: str) -> None:
        """A deleted bucket's files go now instead of after the grace period;
        its names are free again."""
        doc = self._deleted(actor, bucket_id)
        self._purge(doc)
        self._record(bucket_id, "BucketPurged", actor, full_name=doc["full_name"])

    def public(self, doc: Dict[str, Any]) -> Dict[str, Any]:
        aliases = [a["_id"] for a in self._names_col.find({"bucket_id": doc["_id"], "kind": ALIAS})]
        return {"bucket_id": doc["_id"], "name": doc["full_name"], "owner": doc["owner"],
                "quota_bytes": doc["quota_bytes"], "aliases": sorted(aliases),
                "created_at": doc["created_at"], "created_by": doc["created_by"]}

    # ── changes ──────────────────────────────────────────────────────────────
    def _check_quota(self, quota_bytes: int) -> int:
        if not isinstance(quota_bytes, int) or quota_bytes <= 0:
            raise InvalidError("a bucket size limit is a positive number of bytes")
        if quota_bytes > self._max_quota:
            raise InvalidError(f"a bucket can be at most {self._max_quota} bytes", code="QUOTA_TOO_BIG")
        return quota_bytes

    def set_quota(self, actor: Actor, full_name: str, quota_bytes: int) -> Dict[str, Any]:
        doc = self.resolve(full_name)
        self._authz.require_bucket(actor, doc["_id"], R.OWNER)
        quota = self._check_quota(quota_bytes)
        self._col.update_one({"_id": doc["_id"]}, {"$set": {"quota_bytes": quota}})
        self._record(doc["_id"], "BucketQuotaChanged", actor, quota_bytes=quota)
        return self.public(self.resolve(full_name))

    def add_alias(self, actor: Actor, full_name: str, alias: str) -> Dict[str, Any]:
        doc = self.resolve(full_name)
        self._authz.require_bucket(actor, doc["_id"], R.OWNER)
        owner_name, name = split_bucket_name(alias)
        self._owner_for(actor, owner_name)          # a name the person controls
        self._take_name(f"{owner_name}/{name}", doc["_id"], ALIAS)
        self._record(doc["_id"], "BucketAliasAdded", actor, alias=f"{owner_name}/{name}")
        return self.public(doc)

    def move_alias(self, actor: Actor, alias: str, target_full_name: str) -> Dict[str, Any]:
        """Point an alias at another bucket (the person must own both)."""
        owner_name, name = split_bucket_name(alias)
        entry = self._names_col.find_one({"_id": f"{owner_name}/{name}"})
        if entry is None or entry["kind"] != ALIAS:
            raise NotFoundError(f"no alias {alias}")
        self._authz.require_bucket(actor, entry["bucket_id"], R.OWNER)
        target = self.resolve(target_full_name)
        self._authz.require_bucket(actor, target["_id"], R.OWNER)
        self._names_col.update_one({"_id": entry["_id"]}, {"$set": {"bucket_id": target["_id"]}})
        self._record(target["_id"], "BucketAliasMoved", actor, alias=entry["_id"], from_bucket=entry["bucket_id"])
        return self.public(target)

    def remove_alias(self, actor: Actor, alias: str) -> None:
        owner_name, name = split_bucket_name(alias)
        entry = self._names_col.find_one({"_id": f"{owner_name}/{name}"})
        if entry is None or entry["kind"] != ALIAS:
            raise NotFoundError(f"no alias {alias}")
        self._authz.require_bucket(actor, entry["bucket_id"], R.OWNER)
        self._names_col.delete_one({"_id": entry["_id"]})
        self._record(entry["bucket_id"], "BucketAliasRemoved", actor, alias=entry["_id"])

    def share(self, bucket_id: str, principal_id: str, principal_type: str, role: str, by: str) -> None:
        """Called once a sharing invitation is accepted."""
        R.check_bucket_role(role)
        resource = R.bucket_resource(bucket_id)
        self._identity.revoke(resource, principal_id, principal_type, R.ALL_GRANTABLE)
        self._identity.grant(resource, principal_id, principal_type, R.XOLO_BUCKET_PERMISSIONS[role])
        self._access.changed_resource(resource)
        self._history.append(f"bucket-{bucket_id}", "BucketShared", {"bucket_id": bucket_id, "principal_id": principal_id,
                                                                     "principal_type": principal_type, "role": role,
                                                                     "by": by, "at": self._now_fn()})

    def unshare(self, actor: Actor, full_name: str, principal_id: str, principal_type: str = "USER") -> None:
        doc = self.resolve(full_name)
        if principal_id != actor.user_id:
            self._authz.require_bucket(actor, doc["_id"], R.OWNER)
        resource = R.bucket_resource(doc["_id"])
        self._identity.revoke(resource, principal_id, principal_type, R.ALL_GRANTABLE)
        self._access.changed_resource(resource)
        self._record(doc["_id"], "BucketUnshared", actor, principal_id=principal_id)

    def complete_transfer(self, bucket_id: str, new_owner: Dict[str, str], by: str) -> Dict[str, Any]:
        """Both sides agreed (see Transfers). The bucket gets the new owner's
        name; the old full name stays as an alias so nothing breaks."""
        doc = self._col.find_one({"_id": bucket_id})
        if doc is None:
            raise NotFoundError("no such bucket")
        new_full = full_bucket_name(new_owner["name"], doc["name"])
        self._take_name(new_full, bucket_id, NAME)
        self._names_col.update_one({"_id": doc["full_name"]}, {"$set": {"kind": ALIAS}})
        resource = R.bucket_resource(bucket_id)
        if new_owner["kind"] == "user":
            self._identity.transfer(resource, new_owner["id"], "USER")
        else:
            groups = self._teams.groups(new_owner["id"])
            self._identity.transfer(resource, groups["owner"], "GROUP")
            self._identity.grant(resource, groups["admins"], "GROUP", R.XOLO_BUCKET_PERMISSIONS[R.WRITER])
            self._identity.grant(resource, groups["members"], "GROUP", R.XOLO_BUCKET_PERMISSIONS[R.READER])
        self._col.update_one({"_id": bucket_id}, {"$set": {"owner": new_owner, "full_name": new_full}})
        self._access.changed_resource(resource)
        self._history.append(f"bucket-{bucket_id}", "BucketTransferred",
                             {"bucket_id": bucket_id, "from": doc["owner"], "to": new_owner,
                              "old_name": doc["full_name"], "new_name": new_full, "by": by, "at": self._now_fn()})
        return self.public(self._col.find_one({"_id": bucket_id}))

    def delete(self, actor: Actor, full_name: str) -> None:
        """Hidden at once; objects are removed after the grace period (the
        cleanup process). Its names stay reserved meanwhile."""
        doc = self.resolve(full_name)
        self._authz.require_bucket(actor, doc["_id"], R.OWNER)
        now = self._now_fn()
        resource = R.bucket_resource(doc["_id"])
        # Who had access, so a restore gives it back without new invitations.
        saved = [{"principal_id": g.principal_id, "principal_type": g.principal_type,
                  "permissions": sorted(g.permissions)} for g in self._access.grants(resource) if not g.is_owner]
        self._col.update_one({"_id": doc["_id"]}, {"$set": {"deleted": True, "deleted_at": now, "deleted_by": actor.user_id,
                                                           "purge_after": now + self._grace_s, "saved_access": saved}})
        try:
            self._identity.delete_resource(resource)
        finally:
            self._access.changed_resource(resource)
        self._record(doc["_id"], "BucketDeleted", actor, full_name=doc["full_name"])

    def purge_due(self) -> List[str]:
        """Objects of buckets deleted more than the grace period ago."""
        purged = []
        for doc in self._col.find({"deleted": True, "purged": {"$ne": True}, "purge_after": {"$lte": self._now_fn()}}):
            self._purge(doc)
            purged.append(doc["_id"])
        return purged

    def _purge(self, doc: Dict[str, Any]) -> None:
        self._storage.purge_bucket(doc["_id"])
        self._names_col.delete_many({"bucket_id": doc["_id"]})
        self._col.update_one({"_id": doc["_id"]}, {"$set": {"purged": True}, "$unset": {"saved_access": ""}})

    def _record(self, bucket_id: str, event_type: str, actor: Actor, **data: Any) -> None:
        self._history.append(f"bucket-{bucket_id}", event_type,
                             {"bucket_id": bucket_id, "by": actor.user_id, "at": self._now_fn(), **data})


class StorageAccess:
    """Answers axo_storage's question "may this user read/write this bucket
    (from this VE)?" -- the rule agreed in phase 3:

    - the user has the bucket role (directly or through a team): read needs
      Reader+, write needs Writer+;
    - when a job asks (a VE is named), the user is also Developer+ there;
    - platform buckets (``axo/...``): a VE's code bucket only for that VE's
      own VEM; its results bucket (every job's result) written only by
      that VEM (``vem:<ve>``) and read by it and anyone with a role in the
      VE (Viewer+); its objects bucket (big active-object values) only by
      that VE's endpoints (``endpoint:<ve>``), for nobody else; anything
      else only for platform services.

    The answer carries the bucket's storage key (names and aliases resolve
    to it) and size limit. Unknown and deleted buckets are refused."""

    def __init__(self, buckets: Buckets, access: AccessResolver) -> None:
        self._buckets = buckets
        self._access = access

    def check(self, user_id: str, bucket: str, mode: str, virtual_environment_id: Optional[str]) -> Dict[str, Any]:
        if mode not in ("read", "write"):
            return {"allowed": False, "reason": f"unknown mode {mode!r}"}
        if "/" not in bucket:
            # A storage key (bkt-<id>): what the CP itself sends for the bucket
            # pages, after checking the person's role.
            doc = self._buckets.by_key(bucket)
            if doc is None:
                return {"allowed": False, "reason": f"no bucket {bucket}"}
            if user_id == CP_PRINCIPAL and virtual_environment_id is None:
                return {"allowed": True, "reason": "the Control Plane", "bucket_key": doc["_id"],
                        "quota_bytes": doc["quota_bytes"]}
        else:
            try:
                owner, name = split_bucket_name(bucket)
            except InvalidError:
                return {"allowed": False, "reason": "bucket names are written as owner/bucket"}
            if owner == PLATFORM_OWNER:
                return self._platform_bucket(bucket, name, virtual_environment_id, user_id, mode)
            try:
                doc = self._buckets.resolve(bucket)
            except NotFoundError:
                return {"allowed": False, "reason": f"no bucket {bucket}"}
        role = self._access.bucket_role(user_id, doc["_id"])
        needed = R.READER if mode == "read" else R.WRITER
        if not R.at_least(role, needed, R.BUCKET_ROLES):
            return {"allowed": False, "reason": f"the user has no {mode} access to {bucket}",
                    "bucket_key": doc["_id"], "quota_bytes": doc["quota_bytes"]}
        if virtual_environment_id is not None:
            ve_role = self._access.ve_role(user_id, virtual_environment_id)
            if not R.at_least(ve_role, R.DEVELOPER):
                return {"allowed": False, "reason": "the user is not a Developer or higher in this VE",
                        "bucket_key": doc["_id"], "quota_bytes": doc["quota_bytes"]}
        return {"allowed": True, "reason": "granted", "bucket_key": doc["_id"], "quota_bytes": doc["quota_bytes"]}

    def _platform_bucket(self, full_name: str, name: str, virtual_environment_id: Optional[str], user_id: str,
                         mode: str) -> Dict[str, Any]:
        results_ve = ve_of_results_bucket(full_name)
        if results_ve is not None and virtual_environment_id is not None:
            granted = {"allowed": True, "bucket_key": full_name}
            if virtual_environment_id != results_ve:
                return {"allowed": False, "reason": "another VE's results bucket"}
            if user_id == f"vem:{results_ve}":
                return {**granted, "reason": "the VE's own VEM"}
            if mode == "write":
                return {"allowed": False, "reason": "only the VE's VEM writes its results bucket"}
            if R.at_least(self._access.ve_role(user_id, results_ve), R.VIEWER):
                return {**granted, "reason": "a role in the VE"}
            return {"allowed": False, "reason": "the user has no role in this VE"}
        objects_ve = ve_of_objects_bucket(full_name)
        if objects_ve is not None and virtual_environment_id is not None:
            if virtual_environment_id == objects_ve and user_id == f"endpoint:{objects_ve}":
                return {"allowed": True, "reason": "the VE's own endpoints", "bucket_key": full_name}
            return {"allowed": False, "reason": "only the VE's endpoints use its objects bucket"}
        if name.startswith("ve-") and name.endswith("-functions"):
            owner_ve = name[len("ve-"):-len("-functions")]
            if virtual_environment_id in (None, owner_ve):
                return {"allowed": True, "reason": "the VE's own code bucket", "bucket_key": full_name}
            return {"allowed": False, "reason": "another VE's code bucket"}
        if virtual_environment_id is None:
            return {"allowed": True, "reason": "platform service", "bucket_key": full_name}
        return {"allowed": False, "reason": "platform buckets are not for jobs"}
