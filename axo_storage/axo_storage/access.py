from __future__ import annotations

import json
import threading
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Callable, Dict, Optional, Tuple

from option import Err, Ok, Result

from axo_shared.errors import AxoError, StorageAccessCheckUnavailableError

READ = "read"
WRITE = "write"

# VE roles, lowest to highest. Using a bucket from a job needs Developer+.
VE_ROLES = ("viewer", "developer", "admin", "owner")
MIN_VE_ROLE_FOR_DATA = "developer"


@dataclass(frozen=True)
class AccessQuery:
    """May ``user_id`` read/write ``bucket``? ``virtual_environment_id`` is set
    when a job asks (through its endpoint): then the user must also be a
    Developer or higher in that VE. None = a platform service asking for a
    person directly (bucket pages), where only bucket access matters."""

    user_id: str
    bucket: str
    mode: str  # READ | WRITE
    virtual_environment_id: Optional[str] = None


@dataclass(frozen=True)
class AccessDecision:
    allowed: bool
    reason: str = ""
    # The bucket's quota, from whoever owns bucket metadata (the Control
    # Plane). None = not given; the service then falls back to its own.
    quota_bytes: Optional[int] = None
    # Where the bucket is stored here. The Control Plane resolves names and
    # aliases ("alice/reports", an old name after a transfer) to one fixed
    # key; None = the name itself.
    bucket_key: Optional[str] = None


class AccessChecker(ABC):
    """Answers AccessQuery. The real one (phase 5) asks the Control Plane,
    which asks Xolo -- only the Control Plane talks to Xolo. Err means the
    question couldn't be answered at all; the service then refuses (fail closed)."""

    @abstractmethod
    def check(self, query: AccessQuery) -> Result[AccessDecision, AxoError]:
        ...


class AllowAllAccessChecker(AccessChecker):
    """Local development only: everyone may do everything."""

    def check(self, query: AccessQuery) -> Result[AccessDecision, AxoError]:
        return Ok(AccessDecision(allowed=True, reason="allow_all"))


class StaticAccessChecker(AccessChecker):
    """Stand-in for the Control Plane until phase 5: the same rules, with
    grants, teams and VE roles read from a JSON document::

        {
          "teams": {"ml-lab": ["luis", "ana"]},
          "buckets": {
            "sales": {"quota_bytes": 10737418240,
                      "grants": {"user:luis": "read", "team:ml-lab": "read_write"}}
          },
          "virtual_environments": {
            "ml-prod": {"user:luis": "developer", "team:ml-lab": "viewer"}
          }
        }

    Bucket grants: "owner" / "admin" / "read_write" (all imply read + write),
    "read", "write". ``bucket_owner_fn`` (bucket -> user id) makes whoever
    created a bucket its owner -- buckets created from the current UI aren't
    in the file. The best grant across the user and their teams wins.
    """

    _BUCKET_GRANTS = {
        "owner": {READ, WRITE}, "admin": {READ, WRITE}, "read_write": {READ, WRITE},
        "read": {READ}, "write": {WRITE},
    }

    def __init__(
        self,
        document: Dict,
        bucket_owner_fn: Optional[Callable[[str], Optional[str]]] = None,
    ) -> None:
        self._teams: Dict[str, set] = {team: set(members) for team, members in document.get("teams", {}).items()}
        self._buckets: Dict[str, Dict] = document.get("buckets", {})
        self._ves: Dict[str, Dict[str, str]] = document.get("virtual_environments", {})
        self._bucket_owner_fn = bucket_owner_fn

    @classmethod
    def from_file(cls, path: str, bucket_owner_fn: Optional[Callable[[str], Optional[str]]] = None) -> "StaticAccessChecker":
        with open(path, "r", encoding="utf-8") as f:
            return cls(json.load(f), bucket_owner_fn)

    def _principals(self, user_id: str) -> set:
        teams = {team for team, members in self._teams.items() if user_id in members}
        return {f"user:{user_id}"} | {f"team:{team}" for team in teams}

    def check(self, query: AccessQuery) -> Result[AccessDecision, AxoError]:
        principals = self._principals(query.user_id)
        bucket = self._buckets.get(query.bucket, {})
        quota = bucket.get("quota_bytes")

        rights: set = set()
        for principal, grant in bucket.get("grants", {}).items():
            if principal in principals:
                rights |= self._BUCKET_GRANTS.get(grant, set())
        if self._bucket_owner_fn is not None and self._bucket_owner_fn(query.bucket) == query.user_id:
            rights |= {READ, WRITE}
        if query.mode not in rights:
            return Ok(AccessDecision(False, f"user has no {query.mode} access to bucket", quota))

        if query.virtual_environment_id is not None:
            members = self._ves.get(query.virtual_environment_id, {})
            ranks = [VE_ROLES.index(role) for p, role in members.items() if p in principals and role in VE_ROLES]
            if not ranks or max(ranks) < VE_ROLES.index(MIN_VE_ROLE_FOR_DATA):
                return Ok(AccessDecision(False, "user is not a Developer or higher in this VE", quota))
        return Ok(AccessDecision(True, "granted", quota))


class CachingAccessChecker(AccessChecker):
    """Remembers each answer for ``ttl_seconds`` so a job streaming many
    chunks asks the Control Plane once, not once per chunk -- while a
    revoked access still takes effect within seconds, even mid-run.
    Failures to answer are never cached."""

    def __init__(self, inner: AccessChecker, ttl_seconds: float = 5.0, now_fn: Callable[[], float] = time.monotonic) -> None:
        self._inner = inner
        self._ttl = ttl_seconds
        self._now_fn = now_fn
        self._cache: Dict[AccessQuery, Tuple[float, AccessDecision]] = {}
        self._lock = threading.Lock()

    def check(self, query: AccessQuery) -> Result[AccessDecision, AxoError]:
        now = self._now_fn()
        with self._lock:
            hit = self._cache.get(query)
            if hit is not None and now - hit[0] < self._ttl:
                return Ok(hit[1])
        try:
            result = self._inner.check(query)
        except Exception as exc:  # a checker bug or network failure must not crash a request
            return Err(StorageAccessCheckUnavailableError(str(exc), context={"bucket": query.bucket}))
        if result.is_ok:
            with self._lock:
                if len(self._cache) > 10_000:
                    self._cache.clear()
                self._cache[query] = (now, result.unwrap())
        return result


class ControlPlaneAccessChecker(AccessChecker):
    """The real checker: asks the Control Plane (which alone talks to Xolo)
    over HTTP, with this service's own token. Anything but a clear answer is
    an Err, so the request is refused (fail closed)."""

    def __init__(self, url: str, token: str, timeout_seconds: float = 5.0, transport=None) -> None:
        import httpx

        if not url or not token:
            raise ValueError("the control_plane access checker needs AXO_STORAGE_CP_URI and AXO_STORAGE_CP_CHECK_TOKEN")
        self._url = url.rstrip("/") + "/internal/storage/access"
        self._headers = {"Authorization": f"Bearer {token}"}
        self._http = httpx.Client(timeout=timeout_seconds, transport=transport)

    def check(self, query: AccessQuery) -> Result[AccessDecision, AxoError]:
        import httpx

        try:
            response = self._http.post(self._url, headers=self._headers, json={
                "user_id": query.user_id, "bucket": query.bucket, "mode": query.mode,
                "virtual_environment_id": query.virtual_environment_id,
            })
        except httpx.HTTPError as exc:
            return Err(StorageAccessCheckUnavailableError(f"the Control Plane can't be reached: {exc.__class__.__name__}",
                                                          context={"bucket": query.bucket}))
        if response.status_code != 200:
            return Err(StorageAccessCheckUnavailableError(f"the Control Plane answered {response.status_code}",
                                                          context={"bucket": query.bucket}))
        body = response.json()
        return Ok(AccessDecision(allowed=bool(body.get("allowed")), reason=str(body.get("reason", "")),
                                 quota_bytes=body.get("quota_bytes"), bucket_key=body.get("bucket_key")))
