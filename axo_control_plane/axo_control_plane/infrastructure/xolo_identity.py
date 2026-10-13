from __future__ import annotations

from typing import Any, Dict, List, Optional

import httpx

from axo_control_plane.application.ports import Grant, IdentityProvider, IdentityUser, Session
from axo_control_plane.domain.errors import (
    ConflictError,
    DomainError,
    InvalidError,
    NotFoundError,
    UnauthorizedError,
    UnavailableError,
)


class XoloIdentity(IdentityProvider):
    """Xolo over its REST API (``/api/v4``), as one trusted service: the CP's
    account API key carries the ``service``, ``users`` and ``acl`` scopes.

    - People and sessions: signup / auth / refresh / logout with the user's
      session; checking a session uses Xolo's introspection (the session must
      still exist, so a logged-out token stops working).
    - Resources and groups: Xolo's service routes (``acl/service/...``), no
      user session needed -- the CP has already applied axo's role rules.

    Errors map to the CP's own: 401/403 from a user session -> Unauthorized;
    404 -> NotFound; 409 -> Conflict; 422/400 -> Invalid; anything else or no
    answer -> Unavailable (the caller refuses, never allows)."""

    def __init__(self, api_url: str, account_id: str, api_key: str, scope: str = "AXO",
                 license_expiration: str = "100y", session_expiration: str = "1h",
                 timeout_s: float = 10.0, transport: Optional[httpx.BaseTransport] = None) -> None:
        if not api_key:
            raise ValueError("the Control Plane needs its Xolo API key (AXO_CP_XOLO_API_KEY)")
        self._base = f"{api_url.rstrip('/')}/accounts/{account_id}"
        self._api_key = api_key
        self._scope = scope
        self._license_expiration = license_expiration
        self._session_expiration = session_expiration
        self._http = httpx.Client(timeout=timeout_s, transport=transport)

    # ── plumbing ─────────────────────────────────────────────────────────────
    def _call(self, method: str, path: str, *, json: Any = None, params: Optional[Dict[str, Any]] = None,
              session: Optional[Session] = None, token: str = "", secret: str = "",
              api_key: bool = True, user_call: bool = False) -> Any:
        headers: Dict[str, str] = {}
        if api_key:
            headers["X-API-Key"] = self._api_key
        if session is not None:
            token, secret = session.access_token, session.temporal_secret
        if token:
            headers["Authorization"] = f"Bearer {token}"
            headers["Temporal-Secret-Key"] = secret
        try:
            response = self._http.request(method, f"{self._base}{path}", headers=headers, json=json, params=params)
        except httpx.HTTPError as exc:
            raise UnavailableError(f"Xolo can't be reached: {exc.__class__.__name__}", code="XOLO_DOWN")
        if response.status_code < 400:
            return response.json() if response.content else None
        raise _error(response, user_call)

    # ── people and sessions ──────────────────────────────────────────────────
    def signup(self, username, email, password, first_name="", last_name=""):
        body = {"username": username, "email": email, "password": password, "first_name": first_name or username,
                "last_name": last_name or "-", "profile_photo": "", "scope": self._scope,
                "expiration": self._license_expiration}
        data = self._call("POST", "/users/signup", json=body)
        return IdentityUser(user_id=data["key"], username=username, email=email, first_name=first_name,
                            last_name=last_name)

    def login(self, username, password):
        data = self._call("POST", "/users/auth", user_call=True, json={
            "username": username, "password": password, "scope": self._scope,
            "expiration": self._session_expiration, "renew_token": False})
        return _session(data)

    def introspect(self, access_token, temporal_secret):
        data = self._call("POST", "/users/introspect", token=access_token, secret=temporal_secret, user_call=True)
        return _user(data)

    def refresh(self, access_token, temporal_secret):
        data = self._call("POST", "/users/refresh", token=access_token, secret=temporal_secret, user_call=True,
                          json={"expiration": self._session_expiration})
        return _session(data)

    def logout(self, access_token, temporal_secret, username):
        self._call("POST", "/users/logout", token=access_token, secret=temporal_secret, user_call=True,
                   json={"access_token": access_token, "username": username})

    def change_password(self, username, access_token, temporal_secret, new_password):
        self._call("POST", "/users/password", token=access_token, secret=temporal_secret, user_call=True,
                   json={"username": username, "password": new_password})

    # ── resources ────────────────────────────────────────────────────────────
    def claim(self, resource, owner_id, owner_type="USER"):
        self._call("POST", "/acl/service/claim", json={"resource_id": resource, "owner_id": owner_id,
                                                       "owner_type": owner_type})

    def grant(self, resource, principal_id, principal_type, permissions):
        self._call("POST", "/acl/service/grant", json={"resource_id": resource, "principal_id": principal_id,
                                                       "principal_type": principal_type,
                                                       "permissions": sorted(permissions)})

    def revoke(self, resource, principal_id, principal_type, permissions):
        try:
            self._call("DELETE", "/acl/service/revoke", json={"resource_id": resource, "principal_id": principal_id,
                                                              "principal_type": principal_type,
                                                              "permissions": sorted(permissions)})
        except NotFoundError:
            pass                       # nothing to take away

    def grants(self, resource):
        try:
            data = self._call("GET", f"/acl/service/resources/{resource}/grants")
        except NotFoundError:
            return []
        return [Grant(principal_id=g["principal_id"], principal_type=g["principal_type"],
                      permissions=frozenset(g.get("permissions", [])), is_owner=bool(g.get("is_owner")))
                for g in data.get("grants", [])]

    def transfer(self, resource, principal_id, principal_type):
        self._call("POST", f"/acl/service/resources/{resource}/owner",
                   json={"principal_id": principal_id, "principal_type": principal_type,
                         "keep_previous_permissions": None})

    def delete_resource(self, resource):
        try:
            self._call("DELETE", f"/acl/service/resources/{resource}")
        except NotFoundError:
            pass

    def _user_resources(self, user_id: str) -> Dict[str, Any]:
        """Every page of owned + shared resources, and the user's groups."""
        groups: List[str] = []
        resources: List[str] = []
        page = 1
        while True:
            data = self._call("GET", f"/acl/service/users/{user_id}/resources",
                              params={"owned_page": page, "owned_page_size": 200,
                                      "shared_page": page, "shared_page_size": 200})
            if page == 1:
                groups = [g["id"] for g in data.get("groups", [])]
            owned, shared = data["owned_resources"], data["shared_resources"]
            resources += [r["resource_id"] for r in owned["items"] + shared["items"]]
            if page >= max(owned.get("total_pages", 1), shared.get("total_pages", 1)):
                return {"groups": groups, "resources": resources}
            page += 1

    def resources_of(self, user_id):
        return self._user_resources(user_id)["resources"]

    # ── groups ───────────────────────────────────────────────────────────────
    def create_group(self, name, owner_id):
        return self._call("POST", "/acl/service/groups", json={"name": name, "owner_id": owner_id,
                                                               "description": ""})["group_id"]

    def delete_group(self, group_id):
        try:
            self._call("DELETE", f"/acl/service/groups/{group_id}")
        except NotFoundError:
            pass

    def add_members(self, group_id, user_ids):
        self._call("POST", f"/acl/service/groups/{group_id}/members", json={"members": list(user_ids)})

    def remove_members(self, group_id, user_ids):
        self._call("DELETE", f"/acl/service/groups/{group_id}/members", json={"members": list(user_ids)})

    def transfer_group(self, group_id, user_id):
        self._call("POST", f"/acl/service/groups/{group_id}/owner", json={"user_id": user_id,
                                                                         "keep_previous_as_member": True})

    def groups_of(self, user_id):
        data = self._call("GET", f"/acl/service/users/{user_id}/resources",
                          params={"owned_page_size": 1, "shared_page_size": 1})
        return [g["id"] for g in data.get("groups", [])]


def _user(data: Dict[str, Any]) -> IdentityUser:
    return IdentityUser(user_id=data.get("key") or data.get("user_id"), username=data["username"],
                        email=data.get("email", ""), first_name=data.get("first_name", ""),
                        last_name=data.get("last_name", ""), disabled=bool(data.get("disabled", False)))


def _session(data: Dict[str, Any]) -> Session:
    user = IdentityUser(user_id=data["user_id"], username=data["username"], email=data.get("email", ""),
                        first_name=data.get("first_name", ""), last_name=data.get("last_name", ""))
    return Session(user=user, access_token=data["access_token"], temporal_secret=data["temporal_secret"])


def _error(response: httpx.Response, user_call: bool) -> DomainError:
    try:
        body = response.json()
        detail = body.get("detail", body) if isinstance(body, dict) else body
        if isinstance(detail, dict):
            detail = detail.get("msg") or detail.get("message") or str(detail)
    except ValueError:
        detail = response.text
    message = str(detail)[:300] or f"Xolo answered {response.status_code}"
    status = response.status_code
    if status in (401, 403):
        # A user's bad session / password vs. the CP's own key being refused.
        if user_call:
            return UnauthorizedError(message)
        return UnavailableError(f"Xolo refused the Control Plane's key: {message}", code="XOLO_KEY_REFUSED")
    if status == 404:
        return NotFoundError(message)
    if status == 409:
        return ConflictError(message)
    if status in (400, 422):
        return InvalidError(message)
    return UnavailableError(f"Xolo error {status}: {message}", code="XOLO_ERROR")
