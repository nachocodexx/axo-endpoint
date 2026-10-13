from __future__ import annotations

import inspect
from typing import Any, Dict, Optional


class AxoError(Exception):
    """Base class for all endpoint-level failures.

    Subclasses set ``code`` and ``name`` as class-level attributes.
    Source location (module, function, line) is captured automatically at
    the call site — one frame up from wherever ``AxoError(...)`` is called.
    """

    code: int = 0
    name: str = "AXO_ERROR"

    def __init__(self, message: str = "", context: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(message)
        self.message = message
        self.context: Dict[str, Any] = context if context is not None else {}
        frame = inspect.stack()[1]
        module_file = frame.filename.rsplit("/", 1)[-1]
        self.src_module = module_file[:-3] if module_file.endswith(".py") else module_file
        self.src_fn = frame.function
        self.src_line = frame.lineno

    def to_dict(self) -> Dict[str, Any]:
        """Returns all error fields as a flat dict ready to spread into a log event."""
        d: Dict[str, Any] = {
            "error_code": self.code,
            "error_name": self.name,
            "error_message": self.message,
        }
        if self.context:
            d["error_context"] = self.context
        return d


# ── 5xxx: transport / wire ────────────────────────────────────────────────────

class MalformedFrameCountError(AxoError):
    code = 5001
    name = "MALFORMED_FRAME_COUNT"


class MalformedEnvelopeError(AxoError):
    code = 5002
    name = "MALFORMED_ENVELOPE"


class MalformedMetadataError(AxoError):
    code = 5003
    name = "MALFORMED_METADATA"


class MalformedEventEnvelopeError(AxoError):
    code = 5004
    name = "MALFORMED_EVENT_ENVELOPE"


# ── 91xx: reliable messaging ──────────────────────────────────────────────────

class JournalIOError(AxoError):
    """The local outbox journal could not be read or written."""

    code = 9101
    name = "JOURNAL_IO_ERROR"


class MalformedAckError(AxoError):
    """A reply on a reliable channel carried no usable message_id."""

    code = 9102
    name = "MALFORMED_ACK"


class TransportSendError(AxoError):
    """A message could not be handed to the transport (peer down, buffer full)."""

    code = 9103
    name = "TRANSPORT_SEND_FAILED"


# ── 92xx: metrics ─────────────────────────────────────────────────────────────

class IncompatibleSketchError(AxoError):
    """Two quantile sketches with different relative accuracies can't be merged."""

    code = 9201
    name = "INCOMPATIBLE_SKETCH"


class InvalidMetricValueError(AxoError):
    """A metric value outside what a sketch accepts (negative or not finite)."""

    code = 9202
    name = "INVALID_METRIC_VALUE"


class InvalidTierSpecError(AxoError):
    """A time-tier spec string like "10s:1h,1m:7d" could not be parsed."""

    code = 9203
    name = "INVALID_TIER_SPEC"


# ── 93xx: axo_storage ─────────────────────────────────────────────────────────

class StorageUnauthenticatedError(AxoError):
    """The caller's VE token or service token is missing or wrong."""

    code = 9301
    name = "STORAGE_UNAUTHENTICATED"


class StorageAccessDeniedError(AxoError):
    """The user has no access to this bucket (or, for a job, isn't a Developer
    or higher in the VE it runs in)."""

    code = 9302
    name = "STORAGE_ACCESS_DENIED"


class StorageAccessCheckUnavailableError(AxoError):
    """The access checker (the Control Plane) couldn't be asked -- fail closed."""

    code = 9303
    name = "STORAGE_ACCESS_CHECK_UNAVAILABLE"


class BucketNotFoundError(AxoError):
    code = 9304
    name = "BUCKET_NOT_FOUND"


class BucketAlreadyExistsError(AxoError):
    code = 9305
    name = "BUCKET_ALREADY_EXISTS"


class ObjectNotFoundError(AxoError):
    code = 9306
    name = "OBJECT_NOT_FOUND"


class ObjectVersionExistsError(AxoError):
    """Versions never change once written -- registering an existing one is refused."""

    code = 9307
    name = "OBJECT_VERSION_EXISTS"


class ObjectNotCompleteError(AxoError):
    """The object version is still uploading (or failed) and can't be read yet."""

    code = 9308
    name = "OBJECT_NOT_COMPLETE"


class ObjectAlreadyCompleteError(AxoError):
    """The object version is complete and can no longer be written to."""

    code = 9309
    name = "OBJECT_ALREADY_COMPLETE"


class StorageChunkOutOfRangeError(AxoError):
    code = 9310
    name = "STORAGE_CHUNK_OUT_OF_RANGE"


class StorageChunkSizeError(AxoError):
    code = 9311
    name = "STORAGE_CHUNK_SIZE_MISMATCH"


class StorageQuotaExceededError(AxoError):
    code = 9312
    name = "STORAGE_QUOTA_EXCEEDED"


class ObjectHashMismatchError(AxoError):
    """The bytes received don't hash to the content_hash the writer declared."""

    code = 9313
    name = "OBJECT_HASH_MISMATCH"


class StorageInvalidRequestError(AxoError):
    """A required field is missing or has the wrong type."""

    code = 9314
    name = "STORAGE_INVALID_REQUEST"


class StorageBackendFailureError(AxoError):
    """Reading or writing chunk bytes in the storage layer failed."""

    code = 9315
    name = "STORAGE_BACKEND_FAILURE"


class StorageUnavailableError(AxoError):
    """axo_storage didn't answer in time (client side)."""

    code = 9316
    name = "STORAGE_UNAVAILABLE"
