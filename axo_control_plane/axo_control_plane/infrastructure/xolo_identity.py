from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable, List, Optional, TypeVar

from xolo.client import errors as XE
from xolo.client import models as XM
from xolo.client.client import XoloClient

from axo_control_plane.application.ports import Grant, IdentityProvider, IdentityUser, Session
from axo_control_plane.domain.errors import (
    ConflictError,
    DomainError,
    InvalidError,
    NotFoundError,
    UnauthorizedError,
    UnavailableError,
)

if TYPE_CHECKING:
    from option import Result

T = TypeVar("T")

_PAGE_SIZE = 200


class XoloIdentity(IdentityProvider):
    """Xolo through its Python client (``XoloClient``), as one trusted
    service: the CP's account API key carries the ``service``, ``users``,
    ``acl`` and ``groups`` scopes.

    - People and sessions: signup / auth / refresh / logout / change password
      with the user's own session; checking a session uses Xolo's
      introspection (the session must still exist, so a logged-out token
      stops working).
    - Resources and groups: the client's ``service_*`` calls, no user session
      needed -- the CP has already applied axo's role rules.

    Errors map to the CP's own: unauthorized / access denied from a user
    session -> Unauthorized; not found -> NotFound; already exists / conflict
    -> Conflict; validation -> Invalid; anything else (including Xolo not
    answering) -> Unavailable (the caller refuses, never allows)."""

    def __init__(self, api_url: str, account_id: str, api_key: str, scope: str = "AXO",
                 license_expiration: str = "100y", session_expiration: str = "1h",
                 client: Optional[XoloClient] = None) -> None:
        if not api_key:
            raise ValueError("the Control Plane needs its Xolo API key (AXO_CP_XOLO_API_KEY)")
        self._client = client or XoloClient(account_id=account_id, api_key=api_key, api_url=api_url)
        self._scope = scope
        self._license_expiration = license_expiration
        self._session_expiration = session_expiration

    # ── people and sessions ──────────────────────────────────────────────────
    def signup(self, username, email, password, first_name="", last_name=""):
        created: XM.CreatedUserResponseDTO = _unwrap(self._client.signup(
            username=username, first_name=first_name or username, last_name=last_name or "-", email=email,
            password=password, scope=self._scope, expiration=self._license_expiration))
        return IdentityUser(user_id=created.key, username=username, email=email, first_name=first_name,
                            last_name=last_name)

    def login(self, username, password):
        auth: XM.AuthenticatedDTO = _unwrap(self._client.auth(
            username=username, password=password, scope=self._scope, expiration=self._session_expiration,
            renew_token=False), user_call=True)
        return self._session(auth)

    def introspect(self, access_token, temporal_secret):
        return _user(_unwrap(self._client.introspect_session(access_token, temporal_secret), user_call=True))

    def refresh(self, access_token, temporal_secret):
        auth = _unwrap(self._client.refresh_token(access_token, temporal_secret,
                                                  expiration=self._session_expiration), user_call=True)
        return self._session(auth)

    def logout(self, access_token, temporal_secret, username):
        _unwrap(self._client.logout(username=username, access_token=access_token, token=access_token,
                                    temporal_secret=temporal_secret), user_call=True)

    def change_password(self, username, access_token, temporal_secret, new_password):
        # Xolo ends this session once the password changes.
        _unwrap(self._client.change_password(new_password, access_token, temporal_secret), user_call=True)

    def _session(self, auth: XM.AuthenticatedDTO) -> Session:
        user_id = auth.user_id
        if not user_id:                # older Xolo answers: ask the new session who it is
            user_id = self.introspect(auth.access_token, auth.temporal_secret).user_id
        user = IdentityUser(user_id=user_id, username=auth.username, email=auth.email,
                            first_name=auth.first_name, last_name=auth.last_name)
        return Session(user=user, access_token=auth.access_token, temporal_secret=auth.temporal_secret)

    # ── resources ────────────────────────────────────────────────────────────
    def claim(self, resource, owner_id, owner_type="USER"):
        _unwrap(self._client.service_claim_resource(resource, owner_id, owner_type=owner_type))

    def grant(self, resource, principal_id, principal_type, permissions):
        _unwrap(self._client.service_grant_permission(resource, principal_id, sorted(permissions),
                                                      principal_type=principal_type))

    def revoke(self, resource, principal_id, principal_type, permissions):
        _ignore_missing(lambda: self._client.service_revoke_permission(
            resource, principal_id, sorted(permissions), principal_type=principal_type))   # nothing to take away

    def grants(self, resource):
        data: Optional[XM.ResourceGrantsDTO] = _ignore_missing(
            lambda: self._client.service_list_resource_grants(resource))
        if data is None:
            return []
        return [Grant(principal_id=g.principal_id, principal_type=g.principal_type,
                      permissions=frozenset(g.permissions), is_owner=g.is_owner) for g in data.grants]

    def transfer(self, resource, principal_id, principal_type):
        _unwrap(self._client.service_transfer_resource_owner(resource, principal_id, principal_type=principal_type,
                                                             keep_previous_permissions=None))

    def delete_resource(self, resource):
        result = self._client.service_delete_resource_policy(resource)
        if result.is_err and _missing_policy(result.unwrap_err()):
            return
        _ignore_missing(lambda: result)

    def resources_of(self, user_id):
        """Every page of owned + shared resources."""
        resources: List[str] = []
        page = 1
        while True:
            data: XM.ServiceUserResourcesDTO = _unwrap(self._client.service_get_user_resources(
                user_id, owned_page=page, owned_page_size=_PAGE_SIZE,
                shared_page=page, shared_page_size=_PAGE_SIZE))
            owned, shared = data.owned_resources, data.shared_resources
            resources += [r.resource_id for r in owned.items + shared.items]
            if page >= max(owned.total_pages, shared.total_pages, 1):
                return resources
            page += 1

    # ── groups ───────────────────────────────────────────────────────────────
    def create_group(self, name, owner_id):
        return _unwrap(self._client.service_create_group(name, owner_id)).group_id

    def delete_group(self, group_id):
        _ignore_missing(lambda: self._client.service_delete_group(group_id))

    def add_members(self, group_id, user_ids):
        _unwrap(self._client.service_add_group_members(group_id, list(user_ids)))

    def remove_members(self, group_id, user_ids):
        _unwrap(self._client.service_remove_group_members(group_id, list(user_ids)))

    def transfer_group(self, group_id, user_id):
        _unwrap(self._client.service_transfer_group_owner(group_id, user_id, keep_previous_as_member=True))

    def groups_of(self, user_id):
        data: XM.ServiceUserResourcesDTO = _unwrap(self._client.service_get_user_resources(
            user_id, owned_page_size=1, shared_page_size=1))
        return [g.id for g in data.groups]


def _user(data: XM.UserDTO) -> IdentityUser:
    return IdentityUser(user_id=data.key, username=data.username, email=data.email or "",
                        first_name=data.first_name or "", last_name=data.last_name or "",
                        disabled=bool(data.disabled))


def _unwrap(result: "Result[T, XE.XoloError]", user_call: bool = False) -> T:
    if result.is_ok:
        return result.unwrap()
    raise _error(result.unwrap_err(), user_call)


def _ignore_missing(call: Callable[[], "Result[Any, XE.XoloError]"]) -> Any:
    """For removals: something already gone is what the caller wanted."""
    try:
        return _unwrap(call())
    except NotFoundError:
        return None


def _missing_policy(err: XE.XoloError) -> bool:
    """Xolo 0.1.0a7 answers deleting a missing resource policy with a 500
    DatabaseError ("Resource policy '<id>' not found") instead of a 404."""
    return isinstance(err, XE.DatabaseError) and "not found" in (err.message or "").lower()


def _error(err: XE.XoloError, user_call: bool) -> DomainError:
    message = (err.message or err.default_message)[:300]
    if isinstance(err, (XE.UnauthorizedError, XE.AccessDeniedError, XE.InvalidLicenseError)):
        # A user's bad session / password vs. the CP's own key being refused.
        if user_call:
            return UnauthorizedError(message)
        return UnavailableError(f"Xolo refused the Control Plane's key: {message}", code="XOLO_KEY_REFUSED")
    if isinstance(err, XE.NotFoundError):
        return NotFoundError(message)
    if isinstance(err, (XE.AlreadyExistsError, XE.ConflictError)):
        return ConflictError(message)
    if isinstance(err, XE.ValidationError):
        return InvalidError(message)
    # InternalError also covers "couldn't reach Xolo" (connection refused, ...).
    return UnavailableError(f"Xolo error: {message}", code="XOLO_ERROR")
