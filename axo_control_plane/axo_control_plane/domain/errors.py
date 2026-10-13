from __future__ import annotations

from typing import Any, Dict, Optional


class DomainError(Exception):
    """Expected failures of a Control Plane operation, mapped to one HTTP
    status by the API layer. ``code`` is a stable machine-readable name."""

    status_code = 400
    code = "INVALID"

    def __init__(self, message: str, *, code: Optional[str] = None, details: Optional[Dict[str, Any]] = None):
        super().__init__(message)
        self.message = message
        if code:
            self.code = code
        self.details = dict(details or {})

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {"error": self.code, "message": self.message}
        if self.details:
            out["details"] = self.details
        return out


class InvalidError(DomainError):
    status_code = 422
    code = "INVALID"


class NotFoundError(DomainError):
    status_code = 404
    code = "NOT_FOUND"


class ConflictError(DomainError):
    status_code = 409
    code = "CONFLICT"


class UnauthorizedError(DomainError):
    status_code = 401
    code = "UNAUTHORIZED"


class ForbiddenError(DomainError):
    status_code = 403
    code = "FORBIDDEN"


class UnavailableError(DomainError):
    """A dependency (Xolo, a VEM, axo_storage) can't answer right now. The
    caller should try again later; nothing is ever allowed by default."""

    status_code = 503
    code = "UNAVAILABLE"


class LimitReachedError(DomainError):
    status_code = 429
    code = "LIMIT_REACHED"
