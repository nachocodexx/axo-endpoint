from __future__ import annotations

import hmac
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional

from fastapi import Header, Request, Security
from fastapi.security import APIKeyHeader, HTTPAuthorizationCredentials, HTTPBearer

from axo_control_plane.application.buckets import Buckets, StorageAccess
from axo_control_plane.application.capacity import CapacityLedger
from axo_control_plane.application.catalog import PlanCatalog
from axo_control_plane.application.cleanup import Cleanup
from axo_control_plane.application.front_door import FrontDoor
from axo_control_plane.application.notifications import Notifications
from axo_control_plane.application.people import Actor, People
from axo_control_plane.application.sdk_keys import KEY_PREFIX, SdkKeys
from axo_control_plane.application.settings import UserSettings
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
    bucket_files: Any = None             # BucketFiles
    db: Any = None
    statistics: Any = None               # VeStatistics
    billing: Any = None                  # BillingViews
    settings: Optional[UserSettings] = None
    extra: Dict[str, Any] = field(default_factory=dict)


def services(request: Request) -> Services:
    return request.app.state.services


# Declared as security schemes (not plain headers) so the API page (/docs)
# gets its Authorize button: Swagger never sends a header parameter named
# Authorization. Either the API key alone, or the session's two values.
# People see these keys as "API keys" (the code still says sdk_keys).
SDK_KEY = APIKeyHeader(name="X-API-Key", scheme_name="API key", auto_error=False,
                       description="An API key (axo_...), from the account menu's API keys page.")
SESSION_TOKEN = HTTPBearer(scheme_name="Session token", auto_error=False,
                           description="access_token from POST /auth/login (with the session secret).")
SESSION_SECRET = APIKeyHeader(name="Temporal-Secret-Key", scheme_name="Session secret", auto_error=False,
                              description="temporal_secret from POST /auth/login (with the session token).")


def _credentials(bearer: Optional[HTTPAuthorizationCredentials], temporal_secret: Optional[str],
                 api_key: Optional[str]):
    token = bearer.credentials.strip() if bearer else None
    return token, (temporal_secret or "").strip(), (api_key or "").strip()


def authenticate(svc: Services, token: Optional[str], secret: str, api_key: str) -> Actor:
    """A session (Bearer + Temporal-Secret-Key) or an API key (X-API-Key)."""
    if api_key:
        if not api_key.startswith(KEY_PREFIX):
            raise UnauthorizedError("invalid API key")
        return svc.keys.authenticate(api_key)
    if token and token.startswith(KEY_PREFIX) and not secret:
        return svc.keys.authenticate(token)          # SDKs may send the key as a bearer token
    if not token:
        raise UnauthorizedError("sign in (Authorization: Bearer <token> + Temporal-Secret-Key) or use an API key")
    user = svc.people.verify(token, secret)
    return svc.people.actor_for(user)


def actor_any(request: Request,
              bearer: Optional[HTTPAuthorizationCredentials] = Security(SESSION_TOKEN),
              temporal_secret_key: Optional[str] = Security(SESSION_SECRET),
              x_api_key: Optional[str] = Security(SDK_KEY)) -> Actor:
    """Any signed-in caller, even one who must still change their password."""
    token, secret, key = _credentials(bearer, temporal_secret_key, x_api_key)
    return authenticate(services(request), token, secret, key)


def actor(request: Request,
          bearer: Optional[HTTPAuthorizationCredentials] = Security(SESSION_TOKEN),
          temporal_secret_key: Optional[str] = Security(SESSION_SECRET),
          x_api_key: Optional[str] = Security(SDK_KEY)) -> Actor:
    """A signed-in caller allowed to use the platform."""
    who = actor_any(request, bearer, temporal_secret_key, x_api_key)
    if who.password_change_required:
        raise ForbiddenError("change your password first", code="PASSWORD_CHANGE_REQUIRED")
    return who


def platform_admin(request: Request,
                   bearer: Optional[HTTPAuthorizationCredentials] = Security(SESSION_TOKEN),
                   temporal_secret_key: Optional[str] = Security(SESSION_SECRET),
                   x_api_key: Optional[str] = Security(SDK_KEY)) -> Actor:
    who = actor(request, bearer, temporal_secret_key, x_api_key)
    if who.platform_role != R.PLATFORM_ADMIN or who.key_id is not None:
        raise ForbiddenError("Platform Admins only")
    return who


def storage_caller(request: Request, authorization: Optional[str] = Header(default=None)) -> None:
    """axo_storage asking about access: its own token, compared in constant time."""
    expected = services(request).storage_check_token
    presented = (authorization or "")[7:].strip() if (authorization or "").lower().startswith("bearer ") else ""
    if not expected or not presented or not hmac.compare_digest(presented, expected):
        raise UnauthorizedError("expected Authorization: Bearer <storage check token>")


def session_tokens(bearer: Optional[HTTPAuthorizationCredentials] = Security(SESSION_TOKEN),
                   temporal_secret_key: Optional[str] = Security(SESSION_SECRET)):
    token, secret, _ = _credentials(bearer, temporal_secret_key, None)
    if not token or not secret:
        raise UnauthorizedError("expected Authorization: Bearer <token> and Temporal-Secret-Key")
    return token, secret


def actor_from_request(svc: Services, request: Request) -> Actor:
    """``actor`` for routes that read the raw request (streaming bodies)."""
    raw = request.headers.get("authorization") or ""
    token = raw[7:].strip() if raw.lower().startswith("bearer ") else None
    secret = (request.headers.get("temporal-secret-key") or "").strip()
    key = (request.headers.get("x-api-key") or "").strip()
    who = authenticate(svc, token, secret, key)
    if who.password_change_required:
        raise ForbiddenError("change your password first", code="PASSWORD_CHANGE_REQUIRED")
    return who
