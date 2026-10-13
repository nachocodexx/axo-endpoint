from __future__ import annotations

import hmac
from dataclasses import dataclass
from typing import Callable, Optional

from fastapi import Header, HTTPException, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

# Named security scheme so the OpenAPI schema shows the lock / "Authorize".
bearer_scheme = HTTPBearer(auto_error=False)

ACTING_USER_HEADER = "X-Acting-User"


@dataclass(frozen=True)
class ActingUser:
    """The person a Control Plane call acts for. The CP has already checked
    their role in this VE (only the CP talks to Xolo); the VEM trusts it and
    uses the id to record who did what and to act for them in axo_storage."""

    key: str


def build_service_token_dependency(service_token: str) -> Callable[..., ActingUser]:
    """A FastAPI dependency accepting only the Control Plane: the request
    must carry ``Authorization: Bearer <service token>`` plus
    ``X-Acting-User: <user id>``. No token configured = every call refused
    (fail closed)."""

    def acting_user(
        bearer: Optional[HTTPAuthorizationCredentials] = Security(bearer_scheme),
        x_acting_user: Optional[str] = Header(default=None, alias=ACTING_USER_HEADER),
    ) -> ActingUser:
        if not service_token:
            raise HTTPException(status_code=401, detail="no service token is configured on this VEM")
        if bearer is None or not hmac.compare_digest(bearer.credentials, service_token):
            raise HTTPException(status_code=401, detail="expected Authorization: Bearer <service token>")
        if not x_acting_user:
            raise HTTPException(status_code=400, detail=f"expected an {ACTING_USER_HEADER} header")
        return ActingUser(key=x_acting_user)

    return acting_user


def token_matches(service_token: str, presented: Optional[str]) -> bool:
    """For the WebSocket handshake (?token=...), where headers can't be set."""
    return bool(service_token) and presented is not None and hmac.compare_digest(presented, service_token)
