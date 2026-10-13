from __future__ import annotations

import hmac
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional

from fastapi import Header, Request

from axo_control_plane.application.buckets import Buckets, StorageAccess
from axo_control_plane.application.capacity import CapacityLedger
from axo_control_plane.application.catalog import PlanCatalog
from axo_control_plane.application.cleanup import Cleanup
from axo_control_plane.application.front_door import FrontDoor
from axo_control_plane.application.notifications import Notifications
from axo_control_plane.application.people import Actor, People
from axo_control_plane.application.sdk_keys import KEY_PREFIX, SdkKeys
from axo_control_plane.application.sharing import Sharing
from axo_control_plane.application.sleeping import SleepManager
from axo_control_plane.application.teams import Teams
from axo_control_plane.application.ve_service import VeService
from axo_control_plane.domain import roles as R
from axo_control_plane.domain.errors import ForbiddenError, UnauthorizedError


@dataclass
class Services:
    """Everything the HTTP layer calls (built once by the server, or by tests)."""

    people: People
    teams: Teams
    ves: VeService
    buckets: Buckets
    sharing: Sharing
    keys: SdkKeys
    notifications: Notifications
    catalog: PlanCatalog
    capacity: CapacityLedger
    front_door: FrontDoor
    sleep: SleepManager
    storage_access: StorageAccess
    cleanup: Optional[Cleanup]
    vem_base_url: Callable[[str], str]
    vem_headers: Callable[[str, str], Dict[str, str]]
    vem_ws_url: Callable[[str, str], str]
    object_store: Any                    # AxoStorageGateway-like (bucket pages)
    storage_check_token: str = ""
    db: Any = None
    extra: Dict[str, Any] = field(default_factory=dict)


def services(request: Request) -> Services:
    return request.app.state.services


def _credentials(authorization: Optional[str], temporal_secret: Optional[str], api_key: Optional[str]):
    token = None
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()
    return token, (temporal_secret or "").strip(), (api_key or "").strip()


def authenticate(svc: Services, token: Optional[str], secret: str, api_key: str) -> Actor:
    """A session (Bearer + Temporal-Secret-Key) or an SDK key (X-API-Key)."""
    if api_key:
        if not api_key.startswith(KEY_PREFIX):
            raise UnauthorizedError("invalid SDK key")
        return svc.keys.authenticate(api_key)
    if token and token.startswith(KEY_PREFIX) and not secret:
        return svc.keys.authenticate(token)          # SDKs may send the key as a bearer token
    if not token:
        raise UnauthorizedError("sign in (Authorization: Bearer <token> + Temporal-Secret-Key) or use an SDK key")
    user = svc.people.verify(token, secret)
    return svc.people.actor_for(user)


def actor_any(request: Request,
              authorization: Optional[str] = Header(default=None),
              temporal_secret_key: Optional[str] = Header(default=None, alias="Temporal-Secret-Key"),
              x_api_key: Optional[str] = Header(default=None, alias="X-API-Key")) -> Actor:
    """Any signed-in caller, even one who must still change their password."""
    token, secret, key = _credentials(authorization, temporal_secret_key, x_api_key)
    return authenticate(services(request), token, secret, key)


def actor(request: Request,
          authorization: Optional[str] = Header(default=None),
          temporal_secret_key: Optional[str] = Header(default=None, alias="Temporal-Secret-Key"),
          x_api_key: Optional[str] = Header(default=None, alias="X-API-Key")) -> Actor:
    """A signed-in caller allowed to use the platform."""
    who = actor_any(request, authorization, temporal_secret_key, x_api_key)
    if who.password_change_required:
        raise ForbiddenError("change your password first", code="PASSWORD_CHANGE_REQUIRED")
    return who


def platform_admin(request: Request,
                   authorization: Optional[str] = Header(default=None),
                   temporal_secret_key: Optional[str] = Header(default=None, alias="Temporal-Secret-Key"),
                   x_api_key: Optional[str] = Header(default=None, alias="X-API-Key")) -> Actor:
    who = actor(request, authorization, temporal_secret_key, x_api_key)
    if who.platform_role != R.PLATFORM_ADMIN or who.key_id is not None:
        raise ForbiddenError("Platform Admins only")
    return who


def storage_caller(request: Request, authorization: Optional[str] = Header(default=None)) -> None:
    """axo_storage asking about access: its own token, compared in constant time."""
    expected = services(request).storage_check_token
    presented = (authorization or "")[7:].strip() if (authorization or "").lower().startswith("bearer ") else ""
    if not expected or not presented or not hmac.compare_digest(presented, expected):
        raise UnauthorizedError("expected Authorization: Bearer <storage check token>")


def session_tokens(authorization: Optional[str] = Header(default=None),
                   temporal_secret_key: Optional[str] = Header(default=None, alias="Temporal-Secret-Key")):
    token, secret, _ = _credentials(authorization, temporal_secret_key, None)
    if not token or not secret:
        raise UnauthorizedError("expected Authorization: Bearer <token> and Temporal-Secret-Key")
    return token, secret


def actor_from_request(svc: Services, request: Request) -> Actor:
    """``actor`` for routes that read the raw request (streaming bodies)."""
    token, secret, key = _credentials(request.headers.get("authorization"),
                                      request.headers.get("temporal-secret-key"), request.headers.get("x-api-key"))
    who = authenticate(svc, token, secret, key)
    if who.password_change_required:
        raise ForbiddenError("change your password first", code="PASSWORD_CHANGE_REQUIRED")
    return who
