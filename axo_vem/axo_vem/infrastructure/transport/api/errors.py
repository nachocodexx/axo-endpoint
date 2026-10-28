from __future__ import annotations

from typing import Optional, Union

from axo_shared.error_catalog import code_of
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from axo_vem.domain.errors import (
    ConflictError,
    DomainError,
    InvalidRequestError,
    NotFoundError,
    NotOwnerError,
    TooManyRequestsError,
    UpstreamTimeoutError,
)
from axo_vem.log import DumbLogger, Log
from axo_vem.log.catalog import Component, Event

_Logger = Union[Log, DumbLogger]


def register_exception_handlers(app: FastAPI, logger: Optional[_Logger] = None) -> None:
    """Translates domain errors raised by application use cases into the
    exact HTTP status codes/detail bodies the old inline
    `raise HTTPException(status_code=..., detail=...)` calls in api/routes/*.py
    used to produce -- centralizing what were previously 4+ duplicated
    inline ownership/existence checks (see migration plan decision E).
    Registered most-specific-first; DomainError is the catch-all fallback
    (maps to 500) for anything not covered by a more specific subclass, e.g.
    application/identity/signup.py's local-persist-failure path.

    Also the single place every domain-error failure across every entity/
    route gets logged with a classified code + description -- error_code is
    just the raised exception's class name (already a stable, meaningful
    identifier here, see domain/errors.py), so no separate mapping table is
    needed."""
    _logger: _Logger = logger or DumbLogger()

    def _log(exc: DomainError, status_code: int) -> None:
        _logger.warning_event(
            Event.Http.REQUEST_FAILED,
            component=Component.HTTP,
            error_code=exc.__class__.__name__,
            description=str(exc),
            status_code=status_code,
        )

    def _body(name: str, message: str, exc: Optional[DomainError] = None, **extra) -> dict:
        """``detail`` stays for older callers; ``error`` / ``error_code`` /
        ``message`` are what every service answers with (the UI links the
        name to its entry in the error catalog). An endpoint's own error
        (e.g. FUNCTION_NOT_FOUND) is passed through when the VEM relayed it."""
        source_name = getattr(exc, "error_name", None)
        source_code = getattr(exc, "error_code", None)
        if source_name:
            name, number = source_name, source_code or code_of(source_name)
        else:
            number = code_of(name)
        return {"error": name, "error_code": number, "message": message, "detail": message, **extra}

    def _domain(name: str, status_code: int):
        async def handler(request: Request, exc: DomainError) -> JSONResponse:
            _log(exc, status_code)
            return JSONResponse(status_code=status_code, content=_body(name, str(exc), exc))
        return handler

    app.add_exception_handler(NotFoundError, _domain("VEM_NOT_FOUND", 404))
    app.add_exception_handler(NotOwnerError, _domain("VEM_FORBIDDEN", 403))
    app.add_exception_handler(ConflictError, _domain("VEM_CONFLICT", 409))
    app.add_exception_handler(InvalidRequestError, _domain("VEM_INVALID", 422))
    app.add_exception_handler(TooManyRequestsError, _domain("VEM_CONFLICT", 429))
    app.add_exception_handler(UpstreamTimeoutError, _domain("ENDPOINT_TIMEOUT", 504))
    app.add_exception_handler(DomainError, _domain("VEM_ERROR", 500))

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        name = _BY_STATUS.get(exc.status_code, "VEM_ERROR")
        message = exc.detail if isinstance(exc.detail, str) else str(exc.detail)
        return JSONResponse(status_code=exc.status_code, content=_body(name, message),
                            headers=getattr(exc, "headers", None))

    @app.exception_handler(RequestValidationError)
    async def _invalid(request: Request, exc: RequestValidationError) -> JSONResponse:
        fields = [{"field": ".".join(str(p) for p in e.get("loc", ())[1:]), "problem": e.get("msg")}
                  for e in exc.errors()]
        message = "; ".join(f"{f['field']}: {f['problem']}" for f in fields) or "invalid request"
        content = _body("VEM_REQUEST_INVALID", message, details={"fields": fields})
        content["detail"] = exc.errors()            # FastAPI's own shape, for older callers
        return JSONResponse(status_code=422, content=content)


# Inline HTTPException(status_code=...) raised by routes -> catalog name.
_BY_STATUS = {400: "VEM_INVALID", 401: "VEM_UNAUTHORIZED", 403: "VEM_FORBIDDEN", 404: "VEM_NOT_FOUND",
              409: "VEM_CONFLICT", 422: "VEM_INVALID", 502: "VEM_BAD_GATEWAY", 503: "VEM_UNAVAILABLE",
              504: "ENDPOINT_TIMEOUT"}
