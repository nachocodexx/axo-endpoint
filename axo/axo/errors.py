"""Errors the axo library returns. ``name`` / ``code`` match the platform's
error catalog (``GET /errors`` on the Control Plane) when the platform
raised them; errors that happen only on the caller's side use code 0."""

from __future__ import annotations

from typing import Any, Dict, Optional


class AxoError(Exception):
    name: str = "AXO_ERROR"
    code: int = 0

    def __init__(self, message: str = "", details: Optional[Dict[str, Any]] = None,
                 name: Optional[str] = None, code: Optional[int] = None, status: Optional[int] = None) -> None:
        super().__init__(message)
        self.message = message
        self.details: Dict[str, Any] = details or {}
        if name is not None:
            self.name = name
        if code is not None:
            self.code = code
        self.status = status

    def __str__(self) -> str:
        return f"{self.name}: {self.message}" if self.message else self.name

    def __repr__(self) -> str:
        return f"{type(self).__name__}({self.name!r}, {self.message!r})"


class AxoApiError(AxoError):
    """The platform answered with an error (``{"error", "error_code", "message", "details"}``)."""

    @classmethod
    def from_response(cls, status: int, body: Any) -> "AxoApiError":
        if isinstance(body, dict) and "error" in body:
            return cls(str(body.get("message") or ""), body.get("details") or {}, name=str(body["error"]),
                       code=int(body.get("error_code") or 0), status=status)
        detail = body.get("detail") if isinstance(body, dict) else body
        return cls(str(detail), name="HTTP_ERROR", status=status)


class AxoConnectionError(AxoError):
    name = "NETWORK_ERROR"
    code = 9901


class AxoJobFailedError(AxoError):
    """The job ran and failed (the function or method raised)."""

    name = "JOB_FAILED"


class AxoRunFailedError(AxoError):
    """A choreography run ended without completing (failed, cancelled, or
    some steps failed); ``details["failed"]`` = step -> error."""

    name = "CHOREOGRAPHY_STEP_FAILED"
    code = 9623


class AxoTimeoutError(AxoError):
    """Waiting for a result took longer than asked (the job may still finish)."""

    name = "WAIT_TIMEOUT"


class AxoCaptureError(AxoError):
    """A class or function can't be sent to the platform."""

    name = "ACTIVE_OBJECT_CLASS_INVALID"
    code = 9410


class AxoMethodNotFoundError(AxoError):
    name = "ACTIVE_OBJECT_METHOD_NOT_FOUND"
    code = 9409


class AxoStoreUnavailableError(AxoError):
    """``self.store`` / ``ctx`` buckets only work while running on the platform."""

    name = "DATAIO_UNAVAILABLE"
    code = 7009
