from __future__ import annotations


class DomainError(Exception):
    """Base type for every error raised out of domain/application code.
    infrastructure/transport/api/errors.py maps subclasses of this to HTTP
    status codes -- new domain errors should subclass this (or one of the
    three below) rather than raising a bare Exception, so that mapping stays
    exhaustive.

    ``error_name`` / ``error_code``: set when the error is an endpoint's own
    answer the VEM relays (e.g. FUNCTION_NOT_FOUND 2001), so the caller sees
    that error rather than a generic one (see axo_shared.error_catalog)."""

    def __init__(self, *args, error_name: str = None, error_code: int = None) -> None:
        super().__init__(*args)
        self.error_name = error_name
        self.error_code = error_code


class NotFoundError(DomainError):
    """An aggregate looked up by id does not exist (or is soft-deleted).
    Maps to HTTP 404."""


class NotOwnerError(DomainError):
    """The caller is not the owner of the aggregate they're trying to act
    on. Maps to HTTP 403."""


class ConflictError(DomainError):
    """The requested operation conflicts with existing state (e.g. a
    UserProfile that already exists). Maps to HTTP 409."""


class InvalidRequestError(DomainError):
    """The request is well formed but its content isn't valid (e.g. code
    that doesn't define the class it names). Maps to HTTP 422."""


class TooManyRequestsError(DomainError):
    """Too much is already waiting (e.g. a choreography's run queue is
    full). Maps to HTTP 429."""


class UpstreamTimeoutError(DomainError):
    """A downstream axo_endpoint node did not respond within the configured
    command timeout, or was unreachable at connect time. Maps to HTTP 504.
    Generic (not compute-specific) since any domain that proxies a Command
    to a node hits the same failure mode -- endpoints.py/buckets.py/jobs.py
    still raise this inline via HTTPException(504) today and are candidates
    to migrate onto this later, out of scope here."""
