from __future__ import annotations

import hmac
from dataclasses import dataclass
from typing import Callable, FrozenSet, Optional

from fastapi import Header, HTTPException, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from axo_vem.domain.visibility import Viewer

# Named security scheme so the OpenAPI schema shows the lock / "Authorize".
bearer_scheme = HTTPBearer(auto_error=False)

ACTING_USER_HEADER = "X-Acting-User"
# Set by the Control Plane's front door (application/front_door.py there).
ACTING_ROLE_HEADER = "X-Acting-Role"
ACTING_NAME_HEADER = "X-Acting-Name"
ACTING_KEY_HEADER = "X-Acting-Key"
ACTING_KEY_NAME_HEADER = "X-Acting-Key-Name"
# The caller's team ids, comma separated (who sees runs shared with teams).
ACTING_TEAMS_HEADER = "X-Acting-Teams"
# VE roles, weakest first.
VE_ROLES = ("viewer", "developer", "admin", "owner")


@dataclass(frozen=True)
class ActingUser:
    """The person a Control Plane call acts for. The CP has already checked
    their role in this VE (only the CP talks to Xolo); the VEM trusts it and
    uses the id to record who did what and to act for them in axo_storage."""

    key: str
    role: Optional[str] = None           # the person's role in this VE (None: a call not from the front door)
    name: Optional[str] = None
    api_key_id: Optional[str] = None     # the SDK key the call came with
    api_key_name: Optional[str] = None
    teams: FrozenSet[str] = frozenset()

    @property
    def viewer(self) -> Viewer:
        """Who is looking, for the "who sees runs and results" checks."""
        return Viewer(user_id=self.key, role=self.role, teams=self.teams)

    def at_least(self, role: str) -> bool:
        """Has at least ``role``. A call without a role (the CP itself, a
        test) counts as allowed: the CP checks roles before passing on."""
        if self.role is None or self.role not in VE_ROLES:
            return self.role is None
        return VE_ROLES.index(self.role) >= VE_ROLES.index(role)

    @property
    def started_by(self) -> dict:
        """How a job this call starts records who started it."""
        if self.api_key_id:
            return {"started_by_type": "api_key", "started_by_id": self.api_key_id,
                    "started_by_name": self.api_key_name or self.name}
        return {"started_by_type": "user", "started_by_id": self.key, "started_by_name": self.name}


def build_service_token_dependency(service_token: str) -> Callable[..., ActingUser]:
    """A FastAPI dependency accepting only the Control Plane: the request
    must carry ``Authorization: Bearer <service token>`` plus
    ``X-Acting-User: <user id>``. No token configured = every call refused
    (fail closed)."""

    def acting_user(
        bearer: Optional[HTTPAuthorizationCredentials] = Security(bearer_scheme),
        x_acting_user: Optional[str] = Header(default=None, alias=ACTING_USER_HEADER),
        x_acting_role: Optional[str] = Header(default=None, alias=ACTING_ROLE_HEADER),
        x_acting_name: Optional[str] = Header(default=None, alias=ACTING_NAME_HEADER),
        x_acting_key: Optional[str] = Header(default=None, alias=ACTING_KEY_HEADER),
        x_acting_key_name: Optional[str] = Header(default=None, alias=ACTING_KEY_NAME_HEADER),
        x_acting_teams: Optional[str] = Header(default=None, alias=ACTING_TEAMS_HEADER),
    ) -> ActingUser:
        if not service_token:
            raise HTTPException(status_code=401, detail="no service token is configured on this VEM")
        if bearer is None or not hmac.compare_digest(bearer.credentials, service_token):
            raise HTTPException(status_code=401, detail="expected Authorization: Bearer <service token>")
        if not x_acting_user:
            raise HTTPException(status_code=400, detail=f"expected an {ACTING_USER_HEADER} header")
        return ActingUser(key=x_acting_user, role=(x_acting_role or None) and x_acting_role.lower(),
                          name=x_acting_name, api_key_id=x_acting_key, api_key_name=x_acting_key_name,
                          teams=frozenset(t.strip() for t in (x_acting_teams or "").split(",") if t.strip()))

    return acting_user


def token_matches(service_token: str, presented: Optional[str]) -> bool:
    """For the WebSocket handshake (?token=...), where headers can't be set."""
    return bool(service_token) and presented is not None and hmac.compare_digest(presented, service_token)
