from __future__ import annotations

import hmac
from dataclasses import dataclass
from typing import Any, Dict, Iterable, Optional

from option import Err, Ok, Result

from axo_shared.axo_storage import derive_ve_token
from axo_shared.errors import AxoError, StorageInvalidRequestError, StorageUnauthenticatedError

VE = "ve"
SERVICE = "service"


@dataclass(frozen=True)
class Caller:
    """Who is asking, once their credentials checked out: an endpoint of VE
    ``virtual_environment_id`` running a job for ``user_id``, or a platform
    service (VEM / Control Plane) acting for ``user_id``."""

    kind: str  # VE | SERVICE
    user_id: str
    virtual_environment_id: Optional[str] = None


class Authenticator:
    """Checks the ``auth`` block every request carries: either
    {"ve_id", "token"} (per-VE token) or {"service_token"}. Until the
    Control Plane issues tokens (phase 5), a VE token is either listed in
    ``ve_tokens`` or derived from ``ve_token_secret`` (derive_ve_token)."""

    def __init__(
        self, ve_tokens: Dict[str, str], service_tokens: Iterable[str], ve_token_secret: str = "",
    ) -> None:
        self._ve_tokens = dict(ve_tokens)
        self._service_tokens = [t for t in service_tokens if t]
        self._ve_token_secret = ve_token_secret

    def authenticate(self, envelope: Dict[str, Any]) -> Result[Caller, AxoError]:
        user_id = envelope.get("user_id")
        if not isinstance(user_id, str) or not user_id:
            return Err(StorageInvalidRequestError("user_id is required", context={"field": "user_id"}))
        auth = envelope.get("auth")
        if not isinstance(auth, dict):
            return Err(StorageUnauthenticatedError("missing credentials"))

        service_token = auth.get("service_token")
        if isinstance(service_token, str) and service_token:
            if any(hmac.compare_digest(service_token, known) for known in self._service_tokens):
                return Ok(Caller(kind=SERVICE, user_id=user_id))
            return Err(StorageUnauthenticatedError("invalid service token"))

        ve_id, token = auth.get("ve_id"), auth.get("token")
        if isinstance(ve_id, str) and ve_id and isinstance(token, str):
            candidates = [self._ve_tokens.get(ve_id)]
            if self._ve_token_secret:
                candidates.append(derive_ve_token(self._ve_token_secret, ve_id))
            if any(expected and hmac.compare_digest(token, expected) for expected in candidates):
                return Ok(Caller(kind=VE, user_id=user_id, virtual_environment_id=ve_id))
            return Err(StorageUnauthenticatedError("invalid VE token", context={"ve_id": ve_id}))
        return Err(StorageUnauthenticatedError("missing credentials"))


def parse_ve_tokens(raw: str) -> Dict[str, str]:
    """"ml-prod=s3cret,ml-test=other" -> {"ml-prod": "s3cret", ...}"""
    tokens: Dict[str, str] = {}
    for item in raw.split(","):
        if "=" in item:
            ve_id, token = item.split("=", 1)
            if ve_id.strip() and token.strip():
                tokens[ve_id.strip()] = token.strip()
    return tokens
