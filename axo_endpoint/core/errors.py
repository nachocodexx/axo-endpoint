from __future__ import annotations

from axo_shared.errors import AxoError

# ── 1xxx: client / validation ─────────────────────────────────────────────────

class MissingFieldError(AxoError):
    code = 1001
    name = "MISSING_FIELD"


class InvalidFieldError(AxoError):
    code = 1002
    name = "INVALID_FIELD"


class UnknownOperationError(AxoError):
    code = 1003
    name = "UNKNOWN_OPERATION"


# ── 2xxx: not found / state ───────────────────────────────────────────────────

class FunctionNotFoundError(AxoError):
    code = 2001
    name = "FUNCTION_NOT_FOUND"


class JobNotFoundError(AxoError):
    code = 2002
    name = "JOB_NOT_FOUND"


class FunctionVersionExistsError(AxoError):
    """A restore asked for a function version that's already registered --
    versions never change once written."""

    code = 2006
    name = "FUNCTION_VERSION_EXISTS"


class EndpointDrainingError(AxoError):
    """This endpoint is draining (about to be removed or stopped) and takes
    no new jobs -- the VEM sends them to another endpoint."""

    code = 2007
    name = "ENDPOINT_DRAINING"


class InvalidStateError(AxoError):
    code = 2003
    name = "INVALID_STATE_TRANSITION"


class ForeignVirtualEnvironmentError(AxoError):
    """A peer from a different Virtual Environment tried to join this
    endpoint's mesh -- endpoints only ever talk within their own VE.
    (2004 was ENDPOINT_BUSY, removed together with VIRTUAL_ENV_ASSIGN.)"""

    code = 2005
    name = "FOREIGN_VIRTUAL_ENVIRONMENT"


# ── 3xxx: runtime / execution ─────────────────────────────────────────────────

class WorkerCrashedError(AxoError):
    code = 3001
    name = "WORKER_CRASHED"


class FunctionExecError(AxoError):
    """The registered function itself raised -- a failure caused by user code
    (billed), as opposed to the worker/container dying (not billed)."""

    code = 3002
    name = "FUNCTION_EXEC_FAILED"


class InvocationError(AxoError):
    code = 3003
    name = "INVOCATION_FAILED"


class ContainerError(AxoError):
    code = 3004
    name = "CONTAINER_ERROR"


class ContainerBootstrapError(AxoError):
    code = 3005
    name = "CONTAINER_BOOTSTRAP_ERROR"


class ContainerCrashError(AxoError):
    code = 3006
    name = "CONTAINER_CRASH_ERROR"


class JobTimeoutError(AxoError):
    """A job's own configured RuntimeSpec.max_duration_seconds elapsed with
    no result -- an intentional, expected outcome (the worker/container is
    presumably still alive, just over its allotted time), distinct from
    WorkerCrashedError/ContainerCrashError, which mean the worker/container
    itself died."""

    code = 3007
    name = "JOB_TIMEOUT"


class JobCancelledError(AxoError):
    """A JOB_CANCEL command deliberately stopped this job -- unlike
    WorkerCrashedError/ContainerCrashError/JobTimeoutError, this outcome was
    requested, not detected, so it must never trigger a retry."""

    code = 3008
    name = "JOB_CANCELLED"


class QueueWaitTimeoutError(AxoError):
    """A job waited longer than its max_wait for a free runner and was
    dropped before it ever ran -- never billed (it consumed no execution)."""

    code = 3009
    name = "QUEUE_WAIT_TIMEOUT"


class RunnerBudgetExhaustedError(AxoError):
    """No room left in this endpoint's CPU/RAM budget for one more runner
    (container) -- the job waits until a runner is freed. Internal signal
    between ContainerSummoner and ContainerFunctionRuntime; it only ever
    reaches a client if the job's max_wait runs out first."""

    code = 3010
    name = "RUNNER_BUDGET_EXHAUSTED"


# ── 4xxx: infrastructure ──────────────────────────────────────────────────────

class QueueFullError(AxoError):
    code = 4001
    name = "QUEUE_FULL"


class DispatcherClosedError(AxoError):
    code = 4002
    name = "DISPATCHER_CLOSED"


class StorageFailureError(AxoError):
    """Wire-safe wrapper a handler raises around a raw StorageBackend failure.

    Not related by inheritance to storage.backend.StorageError (code 8000) --
    see that class's docstring for why they're deliberate siblings, not a
    hierarchy.
    """

    code = 4003
    name = "STORAGE_FAILURE"


# ── 5xxx: transport / wire ────────────────────────────────────────────────────
# (MalformedFrameCountError/MalformedEnvelopeError/MalformedMetadataError moved
# to axo_shared.errors — they're used by axo_shared.wire, which must not
# depend back on axo_endpoint.)

# ── 81xx: job result replication (80xx is axo_shared.storage) ───────────────

class ResultHashMismatchError(AxoError):
    """A JOB_RESULT_SYNC/JOB_RESULT_REPLICATE payload's hash didn't match the
    hash declared alongside it -- the copy is discarded, never stored."""

    code = 8101
    name = "RESULT_HASH_MISMATCH"
